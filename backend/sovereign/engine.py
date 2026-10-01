"""Single entry point used by the API: LP or MIP, with determinism + device policy."""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Callable

import numpy as np

from . import device
from .bnb import MIPOptions, solve_mip
from .ipm import IPMOptions
from .lp import WarmStartCache, solve_lp
from .model import Model

SOLVER_VERSION = "sovereign-ipm 0.3.0"


@dataclass
class SolverConfig:
    precision: str = "mixed"  # mixed | fp64
    deterministic: bool = True
    use_gpu: bool = True  # used only if a CUDA device is present
    tol: float = 1e-8
    max_iter: int = 150
    node_limit: int = 3000
    time_limit: float = 120.0
    gap_rel: float = 1e-4
    gpu_mode: str = "auto"  # auto (size + VRAM policy) | force | off

    def ipm(self, emit_iterates: bool = False) -> IPMOptions:
        return IPMOptions(tol=self.tol, max_iter=self.max_iter, precision=self.precision,
                          use_gpu=self.use_gpu and device.gpu_available(), gpu_mode=self.gpu_mode,
                          emit_iterates=emit_iterates)


def x_digest(x) -> str | None:
    if x is None:
        return None
    return hashlib.sha256(np.ascontiguousarray(np.asarray(x, dtype=np.float64)).tobytes()).hexdigest()


def solve(model: Model, cfg: SolverConfig | None = None,
          iter_cb: Callable[[dict], None] | None = None,
          node_cb: Callable[[dict], None] | None = None,
          warm_cache: WarmStartCache | None = None, use_warm: bool = True,
          emit_iterates: bool = False) -> dict:
    cfg = cfg or SolverConfig()
    with device.deterministic_threads(cfg.deterministic):
        if model.is_mip:
            r = solve_mip(model, cfg.ipm(emit_iterates), MIPOptions(node_limit=cfg.node_limit, time_limit=cfg.time_limit,
                                                        gap_rel=cfg.gap_rel), node_cb, iter_cb)
            ok = r.x is not None
            out = {
                "status": r.status, "objective_internal": r.objective if ok else None,
                "objective": model.display_objective(r.objective) if ok else None,
                "bound": model.display_objective(r.bound) if math.isfinite(r.bound) else None,
                "gap": r.gap if math.isfinite(r.gap) else None, "x": r.x, "y": r.y,
                "iters": r.lp_iters, "nodes": r.nodes, "lp_solves": r.lp_solves, "seconds": r.seconds,
                "log": r.root.log if r.root else [], "history": r.history, "message": r.message,
                "fp64_switch_iter": r.root.fp64_switch_iter if r.root else None,
                "linear_solver": r.root.linear_solver if r.root else "", "max_violation": r.max_violation,
                "start": "lms", "algorithm": r.root.algorithm if r.root else "mehrotra",
                "certificate": r.certificate, "gpu_decision": r.root.gpu_decision if r.root else "",
            }
        else:
            r = solve_lp(model, cfg.ipm(emit_iterates), iter_cb, warm_cache, use_warm)
            ok = r.status == "optimal"
            out = {
                "status": r.status, "objective_internal": r.objective if ok else None,
                "objective": model.display_objective(r.objective) if ok else None,
                "bound": model.display_objective(r.objective) if ok else None, "gap": 0.0 if ok else None,
                "x": r.x if ok else None, "y": r.y if ok else None, "iters": r.iters, "nodes": 0, "lp_solves": 1,
                "seconds": r.seconds, "log": r.log, "history": [], "message": r.message,
                "fp64_switch_iter": r.fp64_switch_iter, "linear_solver": r.linear_solver,
                "max_violation": r.max_violation, "start": r.start, "algorithm": r.algorithm,
                "certificate": r.certificate, "gpu_decision": r.gpu_decision,
            }
    out["x_sha256"] = x_digest(out["x"])
    conds = [e["cond"] for e in out["log"] if e.get("cond")]
    out["cond_estimate"] = conds[-1] if conds else None
    out["cond_warning"] = bool(conds and conds[-1] > 1e14)
    out["device"] = "gpu" if "gpu" in (out["linear_solver"] or "") else "cpu"
    out["config"] = asdict(cfg)
    out["solver_version"] = SOLVER_VERSION
    return out
