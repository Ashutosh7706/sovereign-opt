"""Verify the Gurobi lanes on the GPU laptop (audit #36/#56). Run from backend/:

    pip install gurobipy                      # then install a free academic / trial licence
    python scripts/check_gurobi.py

Prints the gurobipy version and licence status, then tries the GPU parameter set from
GUROBI_GPU_PARAMS (default {"Method": 6, "PDHGGPU": 1}) on the refinery LP relaxation and
reports which parameter names this Gurobi release accepts. Copy the working set into your
environment and record the version in docs/COMPATIBILITY.md.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sovereign.baselines import DEFAULT_GPU_PARAMS, run_gurobi, run_highs  # noqa: E402
from sovereign.models.refinery import build  # noqa: E402

try:
    import gurobipy as gp
except ImportError:
    raise SystemExit("gurobipy not installed: pip install gurobipy (and a licence)")

print("gurobipy", gp.gurobi.version())
try:
    env = gp.Env(empty=True)
    env.setParam("OutputFlag", 0)
    env.start()
    print("licence: OK")
except gp.GurobiError as e:
    raise SystemExit(f"licence problem: {e}")

params = json.loads(os.environ.get("GUROBI_GPU_PARAMS", json.dumps(DEFAULT_GPU_PARAMS)))
m = gp.Model(env=env)
for k, v in params.items():
    try:
        m.setParam(k, v)
        print(f"  param {k}={v}: accepted")
    except gp.GurobiError as e:
        print(f"  param {k}={v}: REJECTED ({e}) - check the reference manual for this version")

model = build().relaxed()
ref = run_highs(model)
for gpu in (False, True):
    r = run_gurobi(model, gpu)
    agree = r["objective"] is not None and abs(r["objective"] - ref["objective"]) <= 1e-6 * (1 + abs(ref["objective"]))
    print(f"{r['name']:32s} {r['status']:12s} {r['seconds']} s  objective {r['objective']}  "
          f"agrees with HiGHS: {agree}  {r.get('note', '')}")
