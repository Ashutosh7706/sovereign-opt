"""Normal-equation linear algebra for the IPM:  (A D A' + delta I) dy = r.

* Mixed precision (Sec. 12.2): the normal matrix is assembled (SpGEMM) and factorized in
  FP32 while the iterate is far from optimal; every solve is then corrected by FP64
  iterative refinement against the exact FP64 operator, so the returned direction is
  FP64-accurate. Nothing is ever factorized below FP32 (no FP8/FP16 Cholesky).
* Sherman-Morrison-Woodbury: dense columns of A (which would fill A D A' completely) are
  split off, M = M_s + U U',  M^-1 r = z - W (I + U'W)^-1 U'z  with  z = M_s^-1 r, W = M_s^-1 U.
* Device policy (dashboard upgrade plan, Sec. 1): the GPU is used only when the problem is big
  enough for the offload to pay (>= GPU_POLICY["min_rows"] rows, measured per machine with
  scripts/find_sweet_spot.py) and fits in free VRAM. Small models stay on the CPU and every
  solve records the reason. On the GPU both the normal-matrix assembly (GEMM) and the Cholesky
  run on the device; the FP64 refinement stays on the CPU.
"""
from __future__ import annotations

import os

import numpy as np
import scipy.linalg as sla
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from . import device
from .tolerances import TOL


class FactorizationError(RuntimeError):
    pass


GPU_FALLBACKS = {"count": 0, "last_reason": None}  # surfaced on /health (audit #57)
# Rows of the normal matrix below which the GPU is not worth it (transfer + launch overhead
# exceeds the work). Set from config at boot; measure it with scripts/find_sweet_spot.py.
GPU_POLICY = {"min_rows": int(os.environ.get("SOVEREIGN_GPU_MIN_ROWS", "2000") or 2000)}


def gpu_warmup() -> str:  # pragma: no cover - needs CUDA
    """Compile/load the CUDA kernels once at boot so the first solve is not charged for it."""
    cp = device.cupy()
    if cp is None:
        return "no GPU"
    from cupyx.scipy.linalg import solve_triangular
    for dt in (np.float32, np.float64):
        a = cp.random.rand(256, 384).astype(dt)
        m = (a * cp.ones(384, dtype=dt)) @ a.T + 256 * cp.eye(256, dtype=dt)
        L = cp.linalg.cholesky(m)
        solve_triangular(L, cp.ones(256, dtype=dt), lower=True)
    cp.cuda.Stream.null.synchronize()
    return "GPU kernels warmed up"


class NormalSolver:
    def __init__(self, A: sp.csr_matrix, dense_threshold: int = 2500, use_gpu: bool = False,
                 gpu_mode: str = "auto"):
        self.m, self.n = A.shape
        self.A = A.tocsr()
        col_nnz = np.diff(self.A.tocsc().indptr)
        limit = max(10, int(0.3 * self.m))
        dense = np.where(col_nnz > limit)[0] if self.m >= 40 else np.array([], dtype=int)
        if len(dense) > min(50, 0.05 * self.n):
            dense = np.array([], dtype=int)  # too many: SMW would not pay off
        self.dense_cols = dense
        mask = np.ones(self.n, bool)
        mask[dense] = False
        self.sparse_cols = np.where(mask)[0]
        self.As = self.A[:, self.sparse_cols].tocsr()
        self.As32 = self.As.astype(np.float32)
        self.Ad = self.A[:, dense].toarray() if len(dense) else None
        self.A_sq = self.A.multiply(self.A).tocsr()
        self.gpu_decision, gpu = self._gpu_policy(use_gpu, gpu_mode)
        # the GPU path always uses dense normal equations (that is what GPUs are good at)
        self.use_dense = self.m <= dense_threshold or gpu
        density = self.A.nnz / max(1, self.m * self.n)
        # dense BLAS kernels beat sparse-matrix call overhead for small or fairly dense problems
        self.dense_ops = self.use_dense and (self.m * self.n <= 4_000_000 or
                                             (density >= 0.02 and self.m * self.n * 8 <= 6e8))
        if self.dense_ops:
            self.A_op = self.A.toarray()
            self.As_op = self.A_op[:, self.sparse_cols]
            self.As_op32 = self.As_op.astype(np.float32)
            self.AT_op = self.A_op.T
        else:
            self.A_op = self.A
            self.AT_op = self.A.T.tocsr()
        self.use_gpu = gpu
        self._A_gpu = {}
        self.d = None
        self.delta = 0.0
        self.precision = "fp64"
        self.cond_estimate = None
        self._kind = "cpu_dense" if self.use_dense else "cpu_sparse"

    def _gpu_policy(self, use_gpu: bool, mode: str) -> tuple[str, bool]:
        m, n = self.m, self.n
        if mode == "off":
            return "CPU: CPU-only mode selected for this solve", False
        if not device.gpu_available():
            return f"CPU: no usable GPU on this machine - {device.describe()['gpu_unavailable_reason'] or 'no CUDA device'}", False
        if not use_gpu:
            return "CPU: GPU not requested for this solve", False
        if mode != "force" and m < GPU_POLICY["min_rows"]:
            return (f"CPU: problem too small for GPU offload ({m} rows < {GPU_POLICY['min_rows']}) - "
                    f"transfer and kernel-launch overhead would exceed the solve itself; this is the "
                    f"correct engineering choice, not a limitation"), False
        free = device.gpu_free_bytes() or 0
        need = 8 * (3 * m * m + m * n)  # M + factor + workspace + A, FP64 worst case
        if need > 0.8 * free:
            return (f"CPU: needs ~{need / 1e9:.1f} GB VRAM for {m} rows, only {free / 1e9:.1f} GB free "
                    f"(beyond this GPU's demo-safe size)"), False
        forced = " (forced)" if mode == "force" else ""
        return f"GPU{forced}: dense normal equations ({m} rows) on {device.describe()['gpu']}", True

    # ------------------------------------------------------------------ factorize
    def factorize(self, d: np.ndarray, precision: str = "fp64") -> None:
        self.d = d
        self.precision = precision
        diag = self.A_sq @ d
        base = max(float(diag.max()) if len(diag) else 1.0, 1.0)
        delta = 1e-14 * base
        if precision == "fp32" and (not np.isfinite(d).all() or d.max() > 1e30 or d.min() < 1e-30):
            precision = "fp64"  # outside FP32 range -> factorize in FP64
        self.precision = precision
        dtype = np.float32 if precision == "fp32" else np.float64
        ds = d[self.sparse_cols]
        for _ in range(8):
            try:
                self._factor_sparse_part(ds, delta, dtype)
                self.delta = delta
                break
            except (np.linalg.LinAlgError, RuntimeError, ValueError):
                if dtype == np.float32:  # escalate precision before adding more regularization
                    dtype, self.precision = np.float64, "fp64"
                    continue
                delta = max(delta * 100.0, 1e-10 * base)
        else:
            raise FactorizationError("normal matrix could not be factorized")
        # Sherman-Morrison-Woodbury for dense columns
        if self.Ad is not None:
            U = self.Ad * np.sqrt(d[self.dense_cols])
            W = np.column_stack([self._solve_s(U[:, k]) for k in range(U.shape[1])])
            C = np.eye(U.shape[1]) + U.T @ W
            self._smw = (U, W, sla.cho_factor(C))
        else:
            self._smw = None

    def _factor_sparse_part(self, ds, delta, dtype):
        if self.use_gpu:
            if self._gpu_factor(ds, delta, dtype):
                return
            self.use_gpu = False  # permanent CPU fallback for this solve
            self.gpu_decision = f"CPU (fallback): {GPU_FALLBACKS['last_reason']}"
        if self.dense_ops:
            As = self.As_op32 if dtype == np.float32 else self.As_op
            Md = (As * ds.astype(dtype)) @ As.T  # GEMM in the working precision
            Md[np.diag_indices_from(Md)] += dtype(delta)
        else:
            As = self.As32 if dtype == np.float32 else self.As
            M = (As @ sp.diags(ds.astype(dtype)) @ As.T).tocsc()  # SpGEMM in the working precision
            M = M + sp.identity(self.m, dtype=dtype, format="csc") * dtype(delta)
        if self.use_dense:
            if not self.dense_ops:
                Md = M.toarray()
            self._cho = sla.cho_factor(Md, lower=True, check_finite=True)
            self._kind = "cpu_dense"
            dg = np.abs(np.diag(self._cho[0]))
            self.cond_estimate = float((dg.max() / max(dg.min(), 1e-300)) ** 2)
        else:
            self._lu = spla.splu(M, permc_spec="MMD_AT_PLUS_A")
            self._kind = "cpu_sparse"
            du = np.abs(self._lu.U.diagonal())
            self.cond_estimate = float(du.max() / max(du.min(), 1e-300))

    def _gpu_factor(self, ds, delta, dtype) -> bool:
        """Assemble M = A D A' AND factorize it on the GPU. Returns False (caller falls back to
        CPU) on VRAM shortage, CUDA errors or a non-finite factor - never takes the solve down."""
        cp = device.cupy()
        m = self.m
        reason = None
        try:
            if self.dense_ops:
                key = np.dtype(dtype).name
                if key not in self._A_gpu:  # upload A once per precision, reuse every iteration
                    self._A_gpu[key] = cp.asarray(np.ascontiguousarray(self.As_op.astype(dtype)))
                Ag = self._A_gpu[key]
                Mg = (Ag * cp.asarray(ds.astype(dtype))) @ Ag.T  # GEMM on the GPU
            else:
                As = self.As32 if dtype == np.float32 else self.As
                Mg = cp.asarray((As @ sp.diags(ds.astype(dtype)) @ As.T).toarray())
            Mg.ravel()[:: m + 1] += dtype(delta)
            L = cp.linalg.cholesky(Mg)
            del Mg
            if bool(cp.isfinite(L).all()):
                self._L = L
                self._kind = "gpu_dense"
                dg = cp.abs(cp.diag(L))
                self.cond_estimate = float((dg.max() / cp.maximum(dg.min(), 1e-300)) ** 2)
                return True
            reason = "GPU Cholesky produced non-finite values"
        except Exception as e:  # cupy.cuda.memory.OutOfMemoryError, CUDARuntimeError, ...
            reason = f"{type(e).__name__}: {e}"[:300]
        GPU_FALLBACKS["count"] += 1
        GPU_FALLBACKS["last_reason"] = reason
        self._L = None
        self._A_gpu.clear()
        return False

    def _solve_s(self, r: np.ndarray) -> np.ndarray:
        dtype = np.float32 if self.precision == "fp32" else np.float64
        rr = r.astype(dtype)
        if self._kind == "cpu_dense":
            out = sla.cho_solve(self._cho, rr, check_finite=False)
        elif self._kind == "gpu_dense":
            cp = device.cupy()
            from cupyx.scipy.linalg import solve_triangular
            z = solve_triangular(self._L, cp.asarray(rr), lower=True)
            out = cp.asnumpy(solve_triangular(self._L.T, z, lower=False))
        else:
            out = self._lu.solve(rr)
        return np.asarray(out).astype(np.float64)

    def _solve_approx(self, r: np.ndarray) -> np.ndarray:
        z = self._solve_s(r)
        if self._smw is not None:
            U, W, Cf = self._smw
            z = z - W @ sla.cho_solve(Cf, U.T @ z)
        return z

    # ------------------------------------------------------------------ exact FP64 operator
    def matvec(self, v: np.ndarray) -> np.ndarray:
        # the EXACT (unregularized) FP64 normal operator: refinement removes both the
        # FP32 rounding error and the bias introduced by the regularization delta
        return self.A_op @ (self.d * (self.AT_op @ v))

    def solve(self, r: np.ndarray, max_refine: int = 10, rtol: float = TOL.refine_rtol):
        """Returns (x, info) with FP64 iterative refinement."""
        x = self._solve_approx(r)
        rn = float(np.linalg.norm(r)) or 1.0
        res = r - self.matvec(x)
        k = 0
        rel = float(np.linalg.norm(res)) / rn
        while rel > rtol and k < max_refine:
            x = x + self._solve_approx(res)
            res = r - self.matvec(x)
            new_rel = float(np.linalg.norm(res)) / rn
            k += 1
            if new_rel > 0.5 * rel:  # refinement stalled
                rel = new_rel
                break
            rel = new_rel
        return x, {"refine_steps": k, "rel_residual": rel, "kind": self._kind}
