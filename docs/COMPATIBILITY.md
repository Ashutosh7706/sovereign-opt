# Compatibility matrix (roadmap #105, #55)

| OS | Python | Compute | Status | Notes |
|---|---|---|---|---|
| Windows 11 Home 10.0.26200 | 3.13.5 | CPU (12 threads), NumPy 2.3.2 / SciPy 1.16.0 | **verified**: full suite (733 pass, 8 GPU tests skipped), TLS serving, browser UI | Long install paths: vendor/replay file names are kept short (Windows MAX_PATH) |
| Ubuntu 22.04 / 24.04 | 3.11, 3.13 | CPU | CI matrix written (`.github/workflows/ci.yml`); not yet run | |
| Windows (CI runner) | 3.11, 3.13 | CPU | CI matrix written; not yet run | |
| GPU laptop (fill in) | | NVIDIA ___, ___ GB VRAM, driver ___, CUDA ___, CuPy ___ | **to verify** (GPU_VALIDATION.md) | |
| Docker `python:3.13-slim` | 3.13 | CPU | Dockerfile written; not built here | |
| Docker `nvidia/cuda:12.6.3-runtime-ubuntu24.04` | 3.12 | GPU, CuPy 13.3 | Dockerfile written; not built here | Host driver must support CUDA 12.6 |
| Kubernetes 1.27+ | n/a | CPU/GPU | Helm chart written; not deployed | Needs a CNI that enforces NetworkPolicy (Calico, Cilium) |

Minimum: Python ≥ 3.11 (NumPy 2.3 requirement). Browsers: current Chrome, Edge or Firefox (ES modules, no build step).
