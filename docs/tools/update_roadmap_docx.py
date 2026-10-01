"""Adds a 'Build status (v0.2.0)' column to every item table of the Master Roadmap and appends a
'Build Reconciliation' section (roadmap item 116). Source of truth: docs/GAP_STATUS.md.

    python docs/tools/update_roadmap_docx.py <input.docx> <output.docx>
"""
import copy
import re
import sys
from collections import Counter
from pathlib import Path

import docx
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[2]
W = qn
NEW_WIDTHS = [450, 2500, 2500, 1050, 950, 2790]  # sums to the 10240 DXA text width
FILL = {"FIXED": ("E2F0D9", "1E5B24"), "DONE (v0.1)": ("E2F0D9", "1E5B24"), "PARTIAL": ("FFF2CC", "7A5A00"),
        "GPU-LAPTOP": ("DDEBF7", "1F4E79"), "DEFERRED": ("EDEDED", "404040"), "NON-CODE": ("EAE4F2", "4B2E83")}
ORDER = ["FIXED", "DONE (v0.1)", "PARTIAL", "GPU-LAPTOP", "DEFERRED", "NON-CODE"]


def clean(md: str) -> str:
    s = re.sub(r"\*\*|`", "", md).strip()
    return s


def status_rows():
    text = (ROOT / "docs" / "GAP_STATUS.md").read_text(encoding="utf-8")
    out = {}
    for n, _item, status, ev in re.findall(r"^\| (\d+) \| ([^|]+)\| ([^|]+)\| ([^|]+)\|", text, re.M):
        status = status.strip()
        primary = min((status.find(k), k) for k in ORDER if k in status)[1]
        ev = clean(ev)
        first = re.split(r"(?<=[a-z0-9)\]%])\. (?=[A-Z])", ev)[0]  # "Sec. 3" is not a sentence end
        if len(first) > 125:
            first = first[:122].rsplit(" ", 1)[0] + "…"
        out[int(n)] = (status, primary, first)
    return out


def set_text(p, parts):
    """Replace the runs of paragraph element p with [(text, bold, color)] keeping the first run's rPr."""
    runs = p.findall(W("w:r"))
    base = copy.deepcopy(runs[0].find(W("w:rPr"))) if runs and runs[0].find(W("w:rPr")) is not None else None
    for r in runs:
        p.remove(r)
    for text, bold, color in parts:
        r = p.makeelement(W("w:r"), {})
        rpr = copy.deepcopy(base) if base is not None else r.makeelement(W("w:rPr"), {})
        for tag in ("w:b", "w:bCs", "w:color"):
            for e in rpr.findall(W(tag)):
                rpr.remove(e)
        if bold:
            rpr.insert(0, rpr.makeelement(W("w:bCs"), {}))
            rpr.insert(0, rpr.makeelement(W("w:b"), {}))
        if color:
            c = rpr.makeelement(W("w:color"), {W("w:val"): color})
            rpr.insert(2 if bold else 0, c)
        r.append(rpr)
        t = r.makeelement(W("w:t"), {qn("xml:space"): "preserve"})
        t.text = text
        r.append(t)
        p.append(r)


def cell_width(tc, w):
    tcw = tc.find(W("w:tcPr")).find(W("w:tcW"))
    tcw.set(W("w:w"), str(w))
    tcw.set(W("w:type"), "dxa")


def shade(tc, fill):
    shd = tc.find(W("w:tcPr")).find(W("w:shd"))
    shd.set(W("w:fill"), fill)
    shd.set(W("w:val"), "clear")


def add_status_column(tbl, rows):
    tbl.find(W("w:tblPr")).find(W("w:tblW")).set(W("w:w"), str(sum(NEW_WIDTHS)))
    grid = tbl.find(W("w:tblGrid"))
    cols = grid.findall(W("w:gridCol"))
    for gc, w in zip(cols, NEW_WIDTHS):
        gc.set(W("w:w"), str(w))
    grid.append(copy.deepcopy(cols[-1]))
    grid.findall(W("w:gridCol"))[-1].set(W("w:w"), str(NEW_WIDTHS[-1]))
    for i, tr in enumerate(tbl.findall(W("w:tr"))):
        tcs = tr.findall(W("w:tc"))
        for tc, w in zip(tcs, NEW_WIDTHS):
            cell_width(tc, w)
        new = copy.deepcopy(tcs[-1])
        cell_width(new, NEW_WIDTHS[-1])
        p = new.find(W("w:p"))
        if i == 0:
            set_text(p, [("Build status (v0.2.0)", True, None)])
        else:
            n = int("".join(x.text or "" for x in tcs[0].iter(W("w:t"))).strip())
            status, primary, note = rows[n]
            fill, ink = FILL[primary]
            shade(new, fill)
            set_text(p, [(status, True, ink), (" — " + note, False, None)])
        tr.append(new)


def clone_para(src, text_parts):
    p = copy.deepcopy(src)
    set_text(p, text_parts)
    return p


def main(src, dst):
    rows = status_rows()
    assert sorted(rows) == list(range(1, 122)), "GAP_STATUS.md must cover items 1-121"
    d = docx.Document(src)
    body = d.element.body
    kids = list(body)
    title, subtitle, callout_ok = kids[0], kids[1], kids[3]
    h1 = next(k for k in kids if k.tag == W("w:p") and k.find(".//" + W("w:pStyle")) is not None
              and k.find(".//" + W("w:pStyle")).get(W("w:val")) == "Heading1")
    h2 = next(k for k in kids if k.tag == W("w:p") and k.find(".//" + W("w:pStyle")) is not None
              and k.find(".//" + W("w:pStyle")).get(W("w:val")) == "Heading2")
    plain = kids[16]
    warn = kids[-2]
    tables = [k for k in kids if k.tag == W("w:tbl")]
    proto_tbl = copy.deepcopy(tables[0])  # untouched 5-column copy for the summary table

    for t in tables:
        add_status_column(t, rows)

    counts = Counter(v[1] for v in rows.values())
    summary = " · ".join(f"{k} {counts[k]}" for k in ORDER)
    subtitle.addnext(clone_para(subtitle, [("Updated 29 September 2026 with the build status of every item "
                                            "(software build v0.2.0). ", True, "1F4E79"),
                                           (f"Summary of all 121 items: {summary}. The new right-hand column "
                                            "gives each item's status and the evidence in one line; the "
                                            "Build Reconciliation section at the end explains what changed, "
                                            "what still needs the GPU laptop, and what was deliberately "
                                            "deferred.", False, None)]))

    sect = body.find(W("w:sectPr"))

    def add(el):
        sect.addprevious(el)

    def H1(t):
        add(clone_para(h1, [(t, False, None)]))

    def H2(t):
        add(clone_para(h2, [(t, False, None)]))

    def P(*parts):
        add(clone_para(plain, [(x, b, c) for x, b, c in parts]))

    H1("Build Reconciliation — v0.2.0 (29 September 2026)")
    P(("This section closes item 116: it reconciles the 121-item list with the code that now exists. "
       "Detailed evidence for every item (test names, files, verification notes) is in docs/GAP_STATUS.md "
       "inside the repository; the shareable test report is docs/TEST_REPORT.html.", False, None))

    # summary table: status | meaning | count
    t = proto_tbl
    trs = t.findall(W("w:tr"))
    for tr in trs[len(ORDER) + 1:]:
        t.remove(tr)
    widths = [1900, 7090, 1250]
    t.find(W("w:tblPr")).find(W("w:tblW")).set(W("w:w"), str(sum(widths)))
    grid = t.find(W("w:tblGrid"))
    for gc in grid.findall(W("w:gridCol"))[3:]:
        grid.remove(gc)
    for gc, w in zip(grid.findall(W("w:gridCol")), widths):
        gc.set(W("w:w"), str(w))
    meaning = {
        "FIXED": "Implemented in this build and covered by an automated test or a recorded verification.",
        "DONE (v0.1)": "Already in the first MVP; re-verified in this build.",
        "PARTIAL": "The useful part is built; the remainder is stated in the item's note.",
        "GPU-LAPTOP": "Code and tooling ready; needs one command on the GPU laptop (docs/GPU_VALIDATION.md).",
        "DEFERRED": "Consciously not built yet; the reason is in the item's note.",
        "NON-CODE": "Business, legal or process work; no software change is appropriate.",
    }
    for i, tr in enumerate(t.findall(W("w:tr"))):
        tcs = tr.findall(W("w:tc"))
        for tc in tcs[3:]:
            tr.remove(tc)
        tcs = tr.findall(W("w:tc"))
        for tc, w in zip(tcs, widths):
            cell_width(tc, w)
        vals = (["Status", "Meaning", "Items"] if i == 0 else
                [ORDER[i - 1], meaning[ORDER[i - 1]], str(counts[ORDER[i - 1]])])
        for j, (tc, v) in enumerate(zip(tcs, vals)):
            p = tc.find(W("w:p"))
            if i == 0:
                set_text(p, [(v, True, None)])
            else:
                fill, ink = FILL[ORDER[i - 1]]
                if j == 0:
                    shade(tc, fill)
                set_text(p, [(v, j == 0, ink if j == 0 else None)])
    add(t)
    add(copy.deepcopy(kids[7]))  # spacer paragraph after a table, as elsewhere in the document

    H2("What changed in the code (highlights)")
    for label, text in [
        ("Solver. ", "Homogeneous self-dual fallback with verified Farkas certificates (39, 43); singleton-row "
                     "presolve with exact dual recovery (41); Curtis-Reid geometric scaling (42); central tolerance "
                     "module (49); condition-number diagnostic (50); deterministic B&B tie-break (47)."),
        ("Security. ", "Operator accounts with scrypt-hashed PINs, operator/supervisor/admin roles (safety-tagged "
                       "sign-off needs a supervisor), CSRF protection, rate limits and lockout, authenticated "
                       "WebSocket, HTTPS tooling, upload caps (21-29)."),
        ("Persistence. ", "SQLite in WAL mode; every state change and its audit entry are written in one "
                          "transaction, so a disk or write failure applies nothing (31, 33); backups with retention, "
                          "external anchoring of the audit chain, versioned replay bundles (32-36)."),
        ("Operations. ", "Structured logs, /health, /metrics, failure alerts, disk checks, graceful shutdown, "
                         "fail-fast configuration, systemd units, Helm chart whose NetworkPolicy denies all egress "
                         "(13, 76-83)."),
        ("NL gate. ", "Red-team set with injection, decimal/unit slips, homoglyphs, relative and time-bounded "
                      "wording; 100% parse accuracy and zero adversarial auto-accepts on the labelled set; "
                      "parser/model version pinned in every audit record; SKU-2 builds refuse cloud mode at boot "
                      "(95-101)."),
        ("Evidence. ", "Tests grew from 77 to 741 (733 pass, 0 fail; 8 real-GPU tests skip on the CPU-only build "
                       "machine); solver-package coverage 87% (IPM 95%, linear algebra 96%, presolve 97%, "
                       "HSD 96%)."),
    ]:
        P((label, True, None), (text, False, None))

    H2("Real defects found by the new tests — and fixed")
    for label, text in [
        ("Badly scaled LPs (42). ", "Rows spanning ten orders of magnitude did not converge: Ruiz scaling alone is "
                                    "not invariant to diagonal scaling. Fixed with Curtis-Reid scaling first."),
        ("False 'infeasible' (42). ", "When the phase-1 check itself failed, the old code reported 'infeasible'. "
                                      "It now reports 'feasibility unknown'."),
        ("Premature 'unbounded' (39). ", "Presolve declared an LP unbounded from an empty column before checking "
                                         "that the rest of the model was feasible."),
        ("Degenerate boundary (39). ", "Refinery plans set exactly at a feasibility boundary that v0.1 reported as "
                                       "'unknown' now solve to optimality through the HSD fallback."),
    ]:
        P((label, True, None), (text, False, None))

    H2("Still open — needs the GPU laptop (one command each)")
    gpu = [n for n, v in sorted(rows.items()) if v[1] == "GPU-LAPTOP"]
    P(("Items " + ", ".join(map(str, gpu)) + ". ", True, None),
      ("Run the full test suite with CuPy installed, scripts/gpu_bench.py (CPU vs GPU factorisation, crossover "
       "size, accuracy, 30-60 minute thermal run, TCO inputs), scripts/check_gurobi.py (licence and GPU "
       "parameter names), and the local-LLM benchmark; then replace every 'not tested on GPU' line with the "
       "measured number. Checklist: docs/GPU_VALIDATION.md.", False, None))

    H2("Deferred, with reasons")
    for n, v in sorted(rows.items()):
        if v[1] == "DEFERRED":
            P((f"Item {n}. ", True, None), (v[2], False, None))

    H2("Corrections to earlier text")
    for label, text in [
        ("Netlib STAIR (11). ", "Earlier versions list STAIR as +2.5126695 x 10^2 flagged MAX. Our recollection of "
                                "Netlib's lp/data/readme is -2.5126695119E+02 in the standard minimise sense; this "
                                "was not verified online. The software never hand-types these values: it reads "
                                "Netlib's readme at run time."),
        ("Items 17 and 61. ", "The code has no shared multi-tenant queue and no NCCL/multi-GPU code, so there was "
                              "nothing to disable or verify; both remain explicitly labelled as not built."),
        ("Dependency choices. ", "To keep air-gapped installs small, several fixes use the standard library "
                                 "instead of the suggested package: scrypt instead of bcrypt (21), built-in CSRF "
                                 "and rate limiting instead of fastapi-csrf-protect/slowapi (23, 24), numbered "
                                 "SQLite migrations instead of Alembic (38), seeded fuzzing instead of Hypothesis "
                                 "(67), a JSON log formatter instead of structlog (76)."),
    ]:
        P((label, True, None), (text, False, None))

    add(clone_para(warn, [("⚠ All speed numbers so far come from a CPU-only machine, where HiGHS is 3-15x faster. "
                           "Do not quote a GPU speed figure until docs/GPU_VALIDATION.md has been completed on the "
                           "GPU laptop; lead the pitch with sovereignty, auditability and explainability.",
                           False, None)]))
    d.save(dst)
    print(f"saved {dst}: {summary}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
