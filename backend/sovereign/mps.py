"""MPS reader (fixed + free format, Netlib compatible) and Netlib reference-value lookup.

Sec. 12.3: benchmark reference optima are NEVER hand-typed. They are either
  (a) parsed from the official Netlib `lp/data/readme` table you place next to the files, or
  (b) computed live by an independent solver (HiGHS) on the same file,
and both are compared in the model's own objective sense (OBJSENSE MAX is honoured).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np
import scipy.sparse as sp

from .model import INF, Model


class MPSError(ValueError):
    pass


def _tokens(line: str) -> list[str]:
    # Whitespace tokenisation reads both free MPS and fixed MPS whose names contain no
    # blanks (true for the whole Netlib LP set).
    return line.split()


MAX_BYTES_DEFAULT = 50_000_000
PARSE_SECONDS_DEFAULT = 20.0


def parse_mps(text: str, name: str | None = None, max_bytes: int = MAX_BYTES_DEFAULT,
              time_limit: float = PARSE_SECONDS_DEFAULT) -> Model:
    """Parse MPS text. Hardened for untrusted uploads (audit #29, #67): size cap, wall-clock
    budget, and every malformed input surfaces as MPSError (never a crash or a hang)."""
    if len(text.encode("utf-8", errors="ignore")) > max_bytes:
        raise MPSError(f"file larger than {max_bytes / 1e6:.0f} MB limit")
    try:
        return _parse(text, name, time.monotonic() + time_limit)
    except MPSError:
        raise
    except (ValueError, IndexError, KeyError, TypeError, OverflowError) as e:
        raise MPSError(f"malformed MPS: {type(e).__name__}: {e}") from None


def _parse(text: str, name: str | None, deadline: float) -> Model:
    section = None
    obj_row = None
    maximize = False
    row_sense: dict[str, str] = {}
    row_order: list[str] = []
    cols: dict[str, int] = {}
    col_order: list[str] = []
    entries: dict[tuple[str, int], float] = {}
    c: dict[int, float] = {}
    rhs: dict[str, float] = {}
    ranges: dict[str, float] = {}
    lb: dict[int, float] = {}
    ub: dict[int, float] = {}
    integer: set[int] = set()
    in_int = False
    obj_const = 0.0
    model_name = name or "mps-model"

    n_rows: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        if lineno % 2000 == 0 and time.monotonic() > deadline:
            raise MPSError(f"parse time limit exceeded at line {lineno}")
        if not raw.strip() or raw.startswith("*"):
            continue
        if not raw[0].isspace():
            parts = raw.split()
            section = parts[0].upper()
            if section == "NAME" and len(parts) > 1:
                model_name = name or parts[1]
            if section == "OBJSENSE" and len(parts) > 1:
                maximize = parts[1].upper().startswith("MAX")
            continue
        t = _tokens(raw)
        if section == "OBJSENSE":
            maximize = t[0].upper().startswith("MAX")
        elif section == "ROWS":
            s, rn = t[0].upper(), t[1]
            if s == "N":
                n_rows.add(rn)
                if obj_row is None:
                    obj_row = rn
                continue
            if s not in ("L", "G", "E"):
                raise MPSError(f"line {lineno}: unknown row type '{s}'")
            row_sense[rn] = s
            row_order.append(rn)
        elif section == "COLUMNS":
            if len(t) >= 3 and t[1].strip("'").upper() == "MARKER":
                tag = t[2].strip("'").upper()
                in_int = tag == "INTORG"
                continue
            cn = t[0]
            if cn not in cols:
                cols[cn] = len(col_order)
                col_order.append(cn)
                if in_int:
                    integer.add(cols[cn])
            j = cols[cn]
            for k in range(1, len(t) - 1, 2):
                rn, v = t[k], float(t[k + 1])
                if rn == obj_row:
                    c[j] = c.get(j, 0.0) + v
                elif rn in row_sense:
                    entries[(rn, j)] = entries.get((rn, j), 0.0) + v
                elif rn not in n_rows:
                    raise MPSError(f"line {lineno}: column {cn} references undefined row '{rn}'")
                # other free (N) rows are ignored
        elif section == "RHS":
            items = t[1:] if len(t) % 2 == 1 else t
            for k in range(0, len(items) - 1, 2):
                rn, v = items[k], float(items[k + 1])
                if rn == obj_row:
                    obj_const = -v  # MPS convention: RHS on objective = -constant
                else:
                    rhs[rn] = v
        elif section == "RANGES":
            items = t[1:] if len(t) % 2 == 1 else t
            for k in range(0, len(items) - 1, 2):
                ranges[items[k]] = float(items[k + 1])
        elif section == "BOUNDS":
            bt = t[0].upper()
            if bt in ("FR", "MI", "PL", "BV"):
                cn = t[2] if len(t) >= 3 else t[1]
                v = None
            else:
                cn, v = (t[2], float(t[3])) if len(t) >= 4 else (t[1], float(t[2]))
            if cn not in cols:
                raise MPSError(f"BOUNDS references unknown column {cn}")
            j = cols[cn]
            if bt == "UP":
                ub[j] = v
                if v < 0 and lb.get(j, 0.0) == 0.0:
                    lb[j] = -INF
            elif bt == "LO":
                lb[j] = v
            elif bt == "FX":
                lb[j] = ub[j] = v
            elif bt == "FR":
                lb[j], ub[j] = -INF, INF
            elif bt == "MI":
                lb[j] = -INF
            elif bt == "PL":
                ub[j] = INF
            elif bt == "BV":
                lb[j], ub[j] = 0.0, 1.0
                integer.add(j)
            elif bt == "LI":
                lb[j] = v; integer.add(j)
            elif bt == "UI":
                ub[j] = v; integer.add(j)
        elif section in ("ENDATA",):
            break

    if not col_order:
        raise MPSError("no COLUMNS found - not an MPS file?")
    rindex = {rn: i for i, rn in enumerate(row_order)}
    # expand ranged rows into two one-sided rows
    names, senses, rvals, map_rows = [], [], [], []
    for rn in row_order:
        s, b = row_sense[rn], rhs.get(rn, 0.0)
        if rn in ranges:
            R = ranges[rn]
            if s == "L":
                lo, hi = b - abs(R), b
            elif s == "G":
                lo, hi = b, b + abs(R)
            else:
                lo, hi = (b, b + R) if R >= 0 else (b + R, b)
            names += [rn + "_lo", rn + "_hi"]; senses += ["G", "L"]; rvals += [lo, hi]
            map_rows += [rindex[rn], rindex[rn]]
        else:
            names.append(rn); senses.append(s); rvals.append(b); map_rows.append(rindex[rn])
    n = len(col_order)
    rows, cc, vals = [], [], []
    expanded: dict[int, list[int]] = {}
    for new_i, orig_i in enumerate(map_rows):
        expanded.setdefault(orig_i, []).append(new_i)
    for (rn, j), v in entries.items():
        for new_i in expanded[rindex[rn]]:
            rows.append(new_i); cc.append(j); vals.append(v)
    A = sp.csr_matrix((vals, (rows, cc)), shape=(len(names), n))
    cvec = np.array([c.get(j, 0.0) for j in range(n)])
    lbv = np.array([lb.get(j, 0.0) for j in range(n)])
    ubv = np.array([ub.get(j, INF) for j in range(n)])
    intv = np.zeros(n, bool)
    for j in integer:
        intv[j] = True
    if not (np.isfinite(A.data).all() and np.isfinite(cvec).all() and np.isfinite(rvals).all()):
        raise MPSError("non-finite coefficient (nan/inf) in COLUMNS, RHS or RANGES")
    if np.isnan(lbv).any() or np.isnan(ubv).any() or (lbv > ubv).any():
        raise MPSError("invalid bounds (nan, or lower bound above upper bound)")
    if maximize:
        cvec, obj_const = -cvec, -obj_const
    return Model(model_name, col_order, cvec, lbv, ubv, intv, A, np.array(senses, dtype="<U1"),
                 np.array(rvals, float), names, [{} for _ in names], obj_const, maximize,
                 {"kind": "mps", "objective_row": obj_row})


def read_mps(path: str | Path) -> Model:
    p = Path(path)
    return parse_mps(p.read_text(errors="replace"), p.stem.upper())


_README_ROW = re.compile(r"^\s*([A-Z0-9][A-Z0-9\-_]*)\s+\d+\s+\d+\s+\d+\s+\d+\s+([-+]?\d\.\d+E[-+]\d+)", re.I)


def netlib_reference_values(readme_text: str) -> dict[str, float]:
    """Parse the optimal-value column of Netlib's lp/data/readme table."""
    out = {}
    for line in readme_text.splitlines():
        m = _README_ROW.match(line)
        if m:
            out[m.group(1).upper()] = float(m.group(2))
    return out
