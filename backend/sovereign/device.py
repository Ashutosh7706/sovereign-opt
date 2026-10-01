"""Compute-device detection, boot diagnostic and determinism controls
(Sec. 2 Tier 3, Sec. 14.1; audit #43/#63, #57).

The platform is CPU-first by design: the GPU path (CuPy/CUDA) is an accelerator, never a
hard dependency, so the solver keeps working if GPU supply is disrupted.
"""
from __future__ import annotations

import contextlib
import os
import platform

import numpy as np

_cupy = None
_gpu = {"name": None, "vram_gb": None, "cuda_runtime": None, "driver": None, "cupy": None}
_reason = "SOVEREIGN_DISABLE_GPU=1" if os.environ.get("SOVEREIGN_DISABLE_GPU") in ("1", "true", "yes") else None
if _reason is None:
    try:  # pragma: no cover - depends on hardware
        import cupy as _cp

        if _cp.cuda.runtime.getDeviceCount() > 0:
            props = _cp.cuda.runtime.getDeviceProperties(0)
            _cupy = _cp
            _gpu.update(name=props["name"].decode(), vram_gb=round(props["totalGlobalMem"] / 1e9, 1),
                        cuda_runtime=_cp.cuda.runtime.runtimeGetVersion(),
                        driver=_cp.cuda.runtime.driverGetVersion(), cupy=_cp.__version__)
        else:
            _reason = "CuPy installed but no CUDA device visible"
    except ImportError:
        _reason = "CuPy not installed (pip install cupy-cuda12x for the GPU path)"
    except Exception as e:  # driver/runtime mismatch etc.
        _reason = f"CUDA initialisation failed: {type(e).__name__}: {e}"


def cupy():
    return _cupy


def gpu_available() -> bool:
    return _cupy is not None


def gpu_free_bytes() -> int | None:  # pragma: no cover - needs CUDA
    if _cupy is None:
        return None
    free, _total = _cupy.cuda.runtime.memGetInfo()
    return int(free)


def describe() -> dict:
    return {
        "gpu": _gpu["name"], "gpu_available": gpu_available(), "vram_gb": _gpu["vram_gb"],
        "cuda_runtime": _gpu["cuda_runtime"], "cuda_driver": _gpu["driver"], "cupy": _gpu["cupy"],
        "gpu_unavailable_reason": _reason,
        "cpu": platform.processor() or platform.machine(), "cpu_threads": os.cpu_count(),
        "os": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
        "numpy": np.__version__,
        "fallback": "CPU (NumPy/SciPy, FP64)" if not gpu_available() else None,
    }


def boot_report() -> str:
    d = describe()
    if d["gpu_available"]:  # pragma: no cover
        return (f"compute: GPU found - {d['gpu']} ({d['vram_gb']} GB VRAM), CUDA runtime {d['cuda_runtime']}, "
                f"driver {d['cuda_driver']}, CuPy {d['cupy']}; CPU FP64 fallback armed")
    return f"compute: no GPU - {d['gpu_unavailable_reason']}. Running CPU FP64 path ({d['cpu_threads']} threads)."


@contextlib.contextmanager
def deterministic_threads(enabled: bool):
    """Deterministic mode pins BLAS to one thread so reductions happen in a fixed
    order; bit-for-bit reproducibility is traded for some raw speed (Sec. 14.1)."""
    if not enabled:
        yield
        return
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:  # pragma: no cover
        yield
        return
    with threadpool_limits(limits=1):
        yield
