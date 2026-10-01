"""v0.3 live dashboard + 3D digital twin (Dashboard_Advanced_Live_Upgrade_Plan, refinery_3d_digital_twin):
twin layout/state correctness, the /ws/twin, /ws/telemetry and upgraded /ws/race streams, and the
size-based GPU policy messages the dashboard shows."""
import numpy as np
import pytest
import scipy.sparse as sp
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sovereign import device, linalg, twin
from sovereign.engine import SolverConfig, solve
from sovereign.models.refinery import build, default_params
from test_api import admin_pin, boot, login


# ------------------------------------------------------------------ twin mapping
def test_twin_layout_is_a_connected_plant():
    lay = twin.layout(default_params())
    ids = {n["id"] for n in lay["nodes"]}
    assert "CDU" in ids and {"Reformer", "FCC", "DHDS-1", "DHDS-2"} <= ids
    assert sum(n["kind"] == "product" for n in lay["nodes"]) == 7
    for e in lay["edges"]:
        assert e["from"] in ids and e["to"] in ids, e
    assert lay["thresholds"] == {"green": 0, "amber": 2, "red": 8}


def test_twin_state_from_optimal_plan_balances_and_prices_bottleneck():
    p = default_params()
    m = build(p)
    r = solve(m)
    assert r["status"] == "optimal"
    st = twin.state(m, r["x"], r["y"], p)
    nodes, flows = st["nodes"], st["flows"]
    # every fill level is a fraction of the physical capacity
    assert all(0.0 <= s["level"] <= 1.0 + 1e-6 for s in nodes.values())
    # shadow prices are shown as a non-negative "value of one more unit" in $/bbl
    assert all(s["dual"] >= 0 for s in nodes.values())
    # crude into the CDU equals the CDU throughput (mass balance of the drawn pipes)
    crude_in = sum(v for k, v in flows.items() if k.endswith("->CDU"))
    assert crude_in == pytest.approx(nodes["CDU"]["value"], rel=1e-6, abs=1e-6)
    # the plan is CDU-limited: the CDU must be the most expensive bottleneck, with the binding reason stated
    top = max(nodes.items(), key=lambda kv: kv[1]["dual"])
    assert top[0] == "CDU" and top[1]["dual"] >= twin.THRESHOLDS["red"] and top[1]["binding"]
    # an unpurchased crude never shows a misleading shadow price
    for k, s in nodes.items():
        if k.startswith("C:") and s.get("parcels", 0) == 0:
            assert s["dual"] == 0


# ------------------------------------------------------------------ GPU policy text (Sec. 1)
def test_gpu_policy_messages_without_gpu(monkeypatch):
    monkeypatch.setattr(device, "gpu_available", lambda: False)
    A = sp.random(40, 80, density=0.2, random_state=1, format="csr")
    assert linalg.NormalSolver(A, use_gpu=False, gpu_mode="off").gpu_decision.startswith("CPU: CPU-only")
    msg = linalg.NormalSolver(A, use_gpu=False, gpu_mode="auto").gpu_decision
    assert msg.startswith("CPU: no usable GPU on this machine")


def test_small_problem_reports_correct_engineering_choice(monkeypatch):
    from test_solver_hardening import _install_fake_gpu
    _install_fake_gpu(monkeypatch, 8 << 30)
    A = sp.random(69, 88, density=0.05, random_state=2, format="csr")
    ns = linalg.NormalSolver(A, use_gpu=True, gpu_mode="auto")
    assert not ns.use_gpu
    assert "too small for GPU offload" in ns.gpu_decision and "correct engineering choice" in ns.gpu_decision


# ------------------------------------------------------------------ live streams over the API
def _drain(ws, stop=("done", "error", "final")):
    out = []
    while True:
        m = ws.receive_json()
        out.append(m)
        if m["type"] in stop:
            return out


def test_live_websockets(tmp_path, monkeypatch):
    A = boot(tmp_path, monkeypatch)
    with TestClient(A.app) as c:
        for path in ("/ws/twin", "/ws/telemetry"):
            with pytest.raises(WebSocketDisconnect) as e:
                with c.websocket_connect(path) as ws:
                    ws.receive_json()
            assert e.value.code == 4401
        login(c, "admin", admin_pin(tmp_path))
        n0 = c.get("/health").json()["audit"]["entries"]

        lay = c.get("/api/twin/layout?stage=production").json()
        assert lay["edges"] == twin.layout(default_params())["edges"]  # shadow plan may differ; production = defaults

        # 3D twin: layout, then live iterate states, then the final plan with exact duals
        with c.websocket_connect("/ws/twin") as ws:
            ws.send_json({"stage": "production"})
            msgs = _drain(ws)
        kinds = [m["type"] for m in msgs]
        assert kinds[0] == "layout" and kinds[-1] == "final" and "iter" in kinds
        it = next(m for m in msgs if m["type"] == "iter")
        assert set(it["state"]) == {"nodes", "flows"} and it["phase"] == "root LP relaxation"
        fin = msgs[-1]
        assert fin["status"] == "optimal" and fin["device"] in ("cpu", "gpu") and fin["gpu_decision"]
        assert fin["state"]["nodes"]["CDU"]["dual"] > 0
        # the audit chain grew (drives the badge pulse) and records the twin solve
        assert c.get("/health").json()["audit"]["entries"] > n0
        assert "twin.solved" in {e["event"] for e in c.get("/api/audit").json()["entries"]}

        # hardware telemetry: real readings or an explicit reason why there are none
        with c.websocket_connect("/ws/telemetry") as ws:
            t = ws.receive_json()
        assert t["type"] == "telemetry" and "solver_cpu_pct" in t and "cpu_threads" in t
        assert t["gpu_available"] or t.get("gpu_note")

        # race: CPU-only mode is honoured and stated; HiGHS-IPM lane present; tree + central-path data stream
        with c.websocket_connect("/ws/race") as ws:
            ws.send_json({"model": "refinery", "device": "cpu", "baselines": ["highs", "highs_ipm"]})
            msgs = _drain(ws, stop=("done", "error"))
        assert msgs[-1]["type"] == "done"
        ours = next(m for m in msgs if m["type"] == "lane_done" and m["lane"] == "sovereign")
        assert ours["gpu_mode"] == "off" and ours["gpu_decision"].startswith("CPU: CPU-only") and ours["device"] == "cpu"
        ipm = next(m for m in msgs if m["type"] == "lane_done" and m["lane"] == "highs_ipm")
        assert ipm["status"] == "not applicable"  # refinery plan is a MIP
        iters = [m for m in msgs if m["type"] == "iter"]
        assert any(m.get("xs_pairs") for m in iters) and any(m.get("obj_model") is not None for m in iters)
        assert all("x_model" not in m for m in iters)  # full vectors never go over the wire
        tree = [m for m in msgs if m["type"] == "tree"]
        assert tree and tree[0]["id"] == 0
        assert {m["state"] for m in tree} <= {"open", "branched", "pruned", "infeasible", "integral"}
        verdict = next(m for m in msgs if m["type"] == "verdict")
        assert verdict["agrees"] is True and verdict["device"] == "cpu"

        # the GPU showcase models are offered
        keys = {m["key"] for m in c.get("/api/models").json()}
        assert {"dense-m", "dense-l", "dense-xl"} <= keys


def test_dashboard_files_are_revalidated(tmp_path, monkeypatch):
    A = boot(tmp_path, monkeypatch)
    with TestClient(A.app) as c:
        r = c.get("/src/twin3d.js")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
        idx = c.get("/").text
        assert "vendor/three.js" in idx and "vendor/orbit.js" in idx  # vendored: works air-gapped


def test_lp_streams_objective_in_model_space():
    """A pure LP streams its objective in model space (what the live ticker shows) plus finite
    (log x, log s) pairs for the central-path plot."""
    from sovereign.models.random_lp import random_lp
    m = random_lp(150, 225, density=0.1)
    seen = []
    r = solve(m, SolverConfig(), lambda e: seen.append(e), None, None, True, True)
    assert r["status"] == "optimal"
    last = [e for e in seen if e.get("obj_model") is not None][-1]
    assert last["obj_model"] == pytest.approx(r["objective"], rel=1e-5, abs=1e-6)
    assert np.isfinite(np.array(last["xs_pairs"])).all()
