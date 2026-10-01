"""Random sparse LPs that are feasible and bounded by construction (race / scaling tests)."""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from ..model import Model

SIZES = {"random-s": (200, 300), "random-m": (800, 1200), "random-l": (2000, 3000)}
# fairly dense LPs: the normal matrix is dense anyway, which is exactly the work a GPU is good at
# (dashboard upgrade plan Sec. 1 - the GPU demo lane belongs on a problem like this, not on the
# 69-row refinery model). Density chosen so the CPU lane still finishes in seconds.
DENSE = {"dense-m": (1500, 2250, 0.10), "dense-l": (3000, 4500, 0.05), "dense-xl": (5000, 7500, 0.03)}


def random_lp(m: int, n: int, density: float | None = None, seed: int = 7) -> Model:
    rng = np.random.default_rng(seed)
    density = density or min(0.2, 8.0 / n)
    A = sp.random(m, n, density=density, random_state=seed, format="csr",
                  data_rvs=lambda k: rng.uniform(-1, 1, k) * 10)
    x0 = rng.uniform(0, 5, n)
    lb = np.zeros(n)
    ub = x0 + rng.uniform(1, 10, n)
    senses = rng.choice(np.array(["L", "G", "E"]), m, p=[0.5, 0.35, 0.15])
    ax = A @ x0
    rhs = np.where(senses == "L", ax + rng.uniform(0, 2, m), np.where(senses == "G", ax - rng.uniform(0, 2, m), ax))
    c = rng.normal(size=n)
    return Model(f"random LP {m}x{n}", [f"x{j}" for j in range(n)], c, lb, ub, np.zeros(n, bool), A,
                 senses.astype("<U1"), rhs, [f"r{i}" for i in range(m)], [{} for _ in range(m)], 0.0, False,
                 {"kind": "random", "seed": seed})
