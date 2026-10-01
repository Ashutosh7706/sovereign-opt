"""Optimization model container + builder.

A Model is always stored as a *minimization*:  min c'x + obj_const
    s.t.  A x  (sense_i)  rhs_i      sense in {'L' (<=), 'G' (>=), 'E' (=)}
          lb <= x <= ub, x_j integer for integer[j]

`maximize=True` only records that the user-facing objective is the negation,
so displayed values can be flipped back (the STAIR-style sign trap, Sec. 12.3).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import scipy.sparse as sp

INF = np.inf


@dataclass
class Model:
    name: str
    var_names: list[str]
    c: np.ndarray
    lb: np.ndarray
    ub: np.ndarray
    integer: np.ndarray
    A: sp.csr_matrix
    senses: np.ndarray  # dtype '<U1'
    rhs: np.ndarray
    row_names: list[str]
    row_meta: list[dict] = field(default_factory=list)
    obj_const: float = 0.0
    maximize: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.c)

    @property
    def m(self) -> int:
        return len(self.rhs)

    @property
    def is_mip(self) -> bool:
        return bool(self.integer.any())

    def display_objective(self, internal_obj: float) -> float:
        """Convert the internal (minimization) objective to the user's sense."""
        return -internal_obj if self.maximize else internal_obj

    def with_bounds(self, lb: np.ndarray, ub: np.ndarray) -> "Model":
        return Model(self.name, self.var_names, self.c, lb, ub, self.integer, self.A,
                     self.senses, self.rhs, self.row_names, self.row_meta,
                     self.obj_const, self.maximize, self.meta)

    def relaxed(self) -> "Model":
        return Model(self.name, self.var_names, self.c, self.lb, self.ub,
                     np.zeros(self.n, dtype=bool), self.A, self.senses, self.rhs,
                     self.row_names, self.row_meta, self.obj_const, self.maximize, self.meta)

    def fingerprint(self) -> str:
        """SHA-256 of the canonical numerical content (replay bundles, Sec. 14.1)."""
        h = hashlib.sha256()
        A = self.A.tocsr()
        A.sort_indices()
        for arr in (self.c, self.lb, self.ub, self.integer.astype(np.int8), A.data,
                    A.indices.astype(np.int64), A.indptr.astype(np.int64), self.rhs):
            h.update(np.ascontiguousarray(arr).tobytes())
        h.update("".join(self.senses.tolist()).encode())
        h.update(json.dumps([self.obj_const, self.maximize]).encode())
        return h.hexdigest()

    # ---- (de)serialisation for replay bundles -------------------------------
    def to_dict(self) -> dict:
        A = self.A.tocoo()
        return {
            "name": self.name, "var_names": self.var_names, "c": self.c.tolist(),
            "lb": [("-inf" if v == -INF else v) for v in self.lb.tolist()],
            "ub": [("inf" if v == INF else v) for v in self.ub.tolist()],
            "integer": self.integer.astype(int).tolist(),
            "A": {"shape": list(A.shape), "row": A.row.tolist(), "col": A.col.tolist(), "data": A.data.tolist()},
            "senses": self.senses.tolist(), "rhs": self.rhs.tolist(), "row_names": self.row_names,
            "row_meta": self.row_meta, "obj_const": self.obj_const, "maximize": self.maximize, "meta": self.meta,
        }

    @staticmethod
    def from_dict(d: dict) -> "Model":
        A = sp.coo_matrix((d["A"]["data"], (d["A"]["row"], d["A"]["col"])), shape=tuple(d["A"]["shape"])).tocsr()
        lb = np.array([-INF if v == "-inf" else float(v) for v in d["lb"]])
        ub = np.array([INF if v == "inf" else float(v) for v in d["ub"]])
        return Model(d["name"], d["var_names"], np.array(d["c"], float), lb, ub,
                     np.array(d["integer"], bool), A, np.array(d["senses"], dtype="<U1"),
                     np.array(d["rhs"], float), d["row_names"], d.get("row_meta", []),
                     d.get("obj_const", 0.0), d.get("maximize", False), d.get("meta", {}))


class ModelBuilder:
    """Tiny named-variable builder used by the refinery model and tests."""

    def __init__(self, name: str, maximize: bool = False):
        self.name = name
        self.maximize = maximize
        self._vars: dict[str, int] = {}
        self._c: list[float] = []
        self._lb: list[float] = []
        self._ub: list[float] = []
        self._int: list[bool] = []
        self._rows: list[tuple[str, dict[int, float], str, float, dict]] = []
        self.obj_const = 0.0

    def var(self, name: str, lb: float = 0.0, ub: float = INF, obj: float = 0.0, integer: bool = False) -> str:
        if name in self._vars:
            raise ValueError(f"duplicate variable {name}")
        self._vars[name] = len(self._c)
        self._c.append(obj)
        self._lb.append(lb)
        self._ub.append(ub)
        self._int.append(integer)
        return name

    def add_obj(self, name: str, coef: float) -> None:
        self._c[self._vars[name]] += coef

    def constr(self, name: str, terms: dict[str, float], sense: str, rhs: float, **meta: Any) -> str:
        assert sense in ("<=", ">=", "=="), sense
        coefs: dict[int, float] = {}
        for v, a in terms.items():
            if a != 0.0:
                j = self._vars[v]
                coefs[j] = coefs.get(j, 0.0) + a
        s = {"<=": "L", ">=": "G", "==": "E"}[sense]
        self._rows.append((name, coefs, s, float(rhs), meta))
        return name

    def fix(self, name: str, value: float) -> None:
        j = self._vars[name]
        self._lb[j] = self._ub[j] = value

    def set_bounds(self, name: str, lb: float | None = None, ub: float | None = None) -> None:
        j = self._vars[name]
        if lb is not None:
            self._lb[j] = lb
        if ub is not None:
            self._ub[j] = ub

    def build(self, meta: dict | None = None) -> Model:
        rows, cols, vals = [], [], []
        for i, (_, coefs, _, _, _) in enumerate(self._rows):
            for j, a in coefs.items():
                rows.append(i)
                cols.append(j)
                vals.append(a)
        m, n = len(self._rows), len(self._c)
        A = sp.csr_matrix((vals, (rows, cols)), shape=(m, n))
        c = np.array(self._c, float)
        if self.maximize:
            c = -c
        return Model(
            name=self.name, var_names=list(self._vars), c=c,
            lb=np.array(self._lb, float), ub=np.array(self._ub, float),
            integer=np.array(self._int, bool), A=A,
            senses=np.array([r[2] for r in self._rows], dtype="<U1"),
            rhs=np.array([r[3] for r in self._rows], float),
            row_names=[r[0] for r in self._rows],
            row_meta=[r[4] for r in self._rows],
            obj_const=-self.obj_const if self.maximize else self.obj_const,
            maximize=self.maximize, meta=meta or {},
        )

    def index(self, name: str) -> int:
        return self._vars[name]
