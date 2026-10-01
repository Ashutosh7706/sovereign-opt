"""Single source of truth for every numerical tolerance (audit #29).

Change a value here, not in the modules. All tolerances are RELATIVE unless noted.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tolerances:
    ipm_opt: float = 1e-8          # primal infeas., dual infeas., rel. gap for "optimal"
    ipm_relaxed: float = 1e-6      # accepted only after an end-game stall (reported in the log)
    fp64_switch: float = 1e-4      # mixed precision: FP32 -> FP64 once ||r_b||/(1+||b||) <= this (Sec. 12.2)
    fp32_accept: float = 1e-8      # an FP32-factor solve is kept only if refined residual <= this
    refine_rtol: float = 1e-13     # iterative-refinement target on the normal equations
    presolve_feas: float = 1e-9    # absolute: lb > ub + this  => infeasible
    int_tol: float = 1e-5          # integrality of a B&B relaxation value
    mip_gap_rel: float = 1e-4
    mip_gap_abs: float = 1e-6
    incumbent_violation: float = 1e-5  # max row violation (x (1+max|b|)) for a B&B incumbent
    phase1_infeasible: float = 1e-6    # phase-1 elastic objective (x (1+||b||_1)) above this => infeasible
    solver_agreement: float = 1e-6     # |ours - reference| / (1+|reference|) counted as "agrees"
    step_to_boundary: float = 0.995    # fraction of the max step taken by the IPM


TOL = Tolerances()
