# Algorithmic basis and deviations (roadmap #53, for technical diligence)

## LP: Mehrotra predictor-corrector (`ipm.py`)
- **Theory.** Mehrotra's predictor-corrector (1992) is the practical form of path-following primal-dual interior-point methods. Feasible short-step and long-step variants converge in O(√n·log(1/ε)) and O(n·log(1/ε)) iterations respectively (Kojima-Mizuno-Yoshise; Monteiro-Adler). Infeasible-start variants converge in polynomial time under the safeguards of Kojima-Megiddo-Mizuno (1993) and Mizuno (1994).
- **Our implementation** follows the standard *practical* Mehrotra scheme:
  - LMS starting point;
  - σ = (μ_aff/μ)³;
  - second-order corrector;
  - separate primal and dual step lengths, scaled by η = max(0.995, 1−μ), capped at 0.9999;
  - upper bounds handled implicitly (w, z pair).

  Like every production IPM (and like Mehrotra's original), it **does not carry a polynomial-complexity guarantee**, because the adaptive centering and the fixed step fraction drop the safeguards the proofs need. Robustness is provided empirically and by the fallback below.
- **Termination:** relative primal and dual residuals and the relative gap below 1e-8. A relaxed 1e-6 tolerance is accepted only after a detected end-game stall (μ collapsed with no residual progress), and it is reported in the log.

## Fallback: homogeneous self-dual model (`hsd.py`)
- **Theory.** Ye-Todd-Mizuno (1994) and Xu-Hung-Ye (1996): the homogeneous embedding always has a strictly complementary solution. An interior-point method on it reaches optimality, or a Farkas certificate of primal or dual infeasibility, in O(√n·log(1/ε)) iterations for the theoretical variant.
- **Our implementation** is the practical Andersen-Andersen (2000) predictor-corrector form, with the same caveat about practical step rules. Primal infeasibility certificates are **re-verified in original variable space** by exact interval arithmetic over the variable box (`lp.verify_farkas`) before they are reported.

## Linear algebra (`linalg.py`)
- Normal equations with dense Cholesky, or SuperLU above 3,500 rows.
- Mixed precision: FP32 factorization with FP64 iterative refinement against the exact operator, escalating to FP64 when refinement stalls. This follows classical mixed-precision iterative refinement (Wilkinson; Moler 1967; Carson-Higham 2018). Accuracy is bounded by the FP64 residual, not by the FP32 factor.
- Dense columns: Sherman-Morrison-Woodbury (standard; Andersen 1996).
- Scaling: Curtis-Reid (1972) least-squares geometric scaling, then Ruiz (2001) equilibration.

## MIP: branch-and-bound (`bnb.py`)
- Best-first, with pseudo-cost branching (Bénichou et al. 1971) and a round-and-fix heuristic.
- **Finite convergence** for bounded integer variables holds because each branch strictly shrinks an integer domain.
- Optimality is claimed only if no node LP failed numerically. Otherwise the status is "feasible, not proven".
- Gap tolerance: 1e-4 relative.

## Novelty statement (roadmap #16)
None of the numerical methods above are novel; all are decades-old published techniques. The
platform's differentiation is in:
- the explainability layer (elastic repair, dual attribution, verified certificates);
- the verification gate around natural-language input;
- the sovereignty and audit architecture.
