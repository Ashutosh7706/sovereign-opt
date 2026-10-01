"""API security + end-to-end flow (audit #21-#25, #28, #29, #68, #71 at API level, #77, #78, #83, #99)."""
import importlib
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def boot(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("SOVEREIGN_DATA", str(tmp_path))
    monkeypatch.setenv("SOVEREIGN_LOG_FORMAT", "text")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app as A
    return importlib.reload(A)


def admin_pin(tmp_path):
    txt = (tmp_path / "BOOTSTRAP_ADMIN.txt").read_text()
    return txt.split("PIN:")[1].split()[0]


def login(c, user, pin):
    r = c.post("/api/auth/login", json={"username": user, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}


def test_security_and_gate_flow(tmp_path, monkeypatch):
    A = boot(tmp_path, monkeypatch)
    with TestClient(A.app) as c:
        # unauthenticated access is refused; health stays public for monitoring
        assert c.get("/api/status").status_code == 401
        assert c.get("/health").json()["status"] in ("ok", "degraded")
        assert "sovereign_http_requests_total" in c.get("/metrics").text
        # bootstrap admin
        h = login(c, "admin", admin_pin(tmp_path))
        st = c.get("/api/status").json()
        assert st["air_gapped"] and st["user"]["role"] == "admin" and not st["cloud_llm_allowed"]
        # CSRF: a write without the header is refused
        assert c.post("/api/users", json={"username": "op1", "pin": "123456", "role": "operator"}).status_code == 403
        assert c.post("/api/users", json={"username": "op1", "pin": "123456", "role": "operator"},
                      headers=h).status_code == 200
        assert c.post("/api/users", json={"username": "sup1", "pin": "654321", "role": "supervisor"},
                      headers=h).status_code == 200
        # SKU-2 lock: cloud-llm cannot be switched on even by an admin
        r = c.post("/api/settings", json={"nl_mode": "cloud-llm"}, headers=h)
        assert r.status_code == 403 and "SKU-2" in r.json()["detail"]
        # operator proposes a safety-tagged constraint and may NOT approve it
        c.post("/api/auth/logout", headers=h)
        h = login(c, "op1", "123456")
        assert c.get("/api/users").status_code == 403
        p = c.post("/api/nl/propose", json={"text": "Tank 3 sulfur limit <= 0.3%"}, headers=h).json()
        assert p["status"] == "pending_signoff" and p["required_role"] == "supervisor" and p["operator"] == "op1"
        r = c.post(f"/api/nl/proposals/{p['id']}/decide", json={"action": "approve"}, headers=h)
        assert r.status_code == 403
        # supervisor signs off; identity comes from the session, not from the request body
        c.post("/api/auth/logout", headers=h)
        h = login(c, "sup1", "654321")
        d = c.post(f"/api/nl/proposals/{p['id']}/decide", json={"action": "approve", "note": "PSE ok",
                                                                 "operator": "someone-else"}, headers=h).json()
        assert d["decided_by"] == "sup1" and d["stage"] == "shadow"
        plan = c.post("/api/plan/solve", headers=h).json()
        assert plan["production"]["status"] == "optimal" and plan["shadow"]["status"] == "optimal"
        # authenticated race over WebSocket
        with c.websocket_connect("/ws/race") as ws:
            ws.send_json({"model": "refinery"})
            seen = []
            while True:
                m = ws.receive_json()
                seen.append(m["type"])
                if m["type"] == "verdict":
                    assert m["agrees"] is True
                if m["type"] in ("done", "error"):
                    break
        assert "iter" in seen and seen[-1] == "done"
        ro = c.post("/api/plan/reoptimize", json={"product_prices": {"HSD": 115}}, headers=h).json()
        assert ro["agree"] and ro["warm"]["start"] == "warm"
        # malformed upload -> clean 400; oversized -> 413
        assert c.post("/api/models/mps", json={"name": "x.mps", "text": "ROWS\n N C\n Q X\n"},
                      headers=h).status_code == 400
        big = {"Content-Length": str(10 ** 9), **h}
        assert c.post("/api/models/mps", content=b"{}", headers=big).status_code == 413
        audit = c.get("/api/audit").json()
        assert audit["verify"]["ok"]
        events = {e["event"] for e in audit["entries"]}
        assert {"auth.login", "nl.proposed", "nl.approved", "plan.solved", "race.completed"} <= events
        assert c.get("/").status_code == 200 and c.get("/docs").status_code == 200


def test_websocket_requires_session(tmp_path, monkeypatch):
    A = boot(tmp_path, monkeypatch)
    with TestClient(A.app) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/race") as ws:
                ws.receive_json()
        assert e.value.code == 4401


def test_login_lockout_and_rate_limit(tmp_path, monkeypatch):
    A = boot(tmp_path, monkeypatch, SOVEREIGN_RATE_PER_MIN="2")
    with TestClient(A.app) as c:
        h = login(c, "admin", admin_pin(tmp_path))
        c.post("/api/users", json={"username": "op2", "pin": "111111", "role": "operator"}, headers=h)
        for _ in range(5):
            assert c.post("/api/auth/login", json={"username": "op2", "pin": "000000"}).status_code == 401
        r = c.post("/api/auth/login", json={"username": "op2", "pin": "111111"})
        assert r.status_code == 401 and "locked" in r.json()["detail"]
        codes = [c.post("/api/plan/solve", headers=h).status_code for _ in range(3)]
        assert codes[:2] == [200, 200] and codes[2] == 429


def test_concurrent_race_clients(tmp_path, monkeypatch):
    A = boot(tmp_path, monkeypatch)
    with TestClient(A.app) as c:
        login(c, "admin", admin_pin(tmp_path))
        results, errors = [], []

        def one():
            try:
                with c.websocket_connect("/ws/race") as ws:
                    ws.send_json({"model": "random-s", "baselines": ["highs"]})
                    while True:
                        m = ws.receive_json()
                        if m["type"] == "verdict":
                            results.append(m["agrees"])
                        if m["type"] in ("done", "error"):
                            break
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=one) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        assert not errors and results == [True] * 4
        assert c.get("/api/audit").json()["verify"]["ok"]


def test_config_fails_fast():
    from sovereign import config
    with pytest.raises(ValueError, match="SKU-2"):
        config.load({"SOVEREIGN_SKU": "SKU-2", "SOVEREIGN_ALLOW_CLOUD_LLM": "1"})
    with pytest.raises(ValueError):
        config.load({"SOVEREIGN_ALERT_THRESHOLD": "zero"})
    s = config.load({"SOVEREIGN_SKU": "sku-1", "SOVEREIGN_ALLOW_CLOUD_LLM": "1", "SOVEREIGN_TYPO": "x"})
    assert s.cloud_allowed and s.unknown_env == ["SOVEREIGN_TYPO"]
