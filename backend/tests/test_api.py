"""API + end-to-end tests for the no-login Sovereign Optimizer build."""

import importlib
import threading

import pytest
from fastapi.testclient import TestClient


# =====================================================================
# Test application bootstrap
# =====================================================================

def boot(tmp_path, monkeypatch, **env):
    """Load the application with an isolated test data directory."""

    monkeypatch.setenv("SOVEREIGN_DATA", str(tmp_path))
    monkeypatch.setenv("SOVEREIGN_LOG_FORMAT", "text")

    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import app as A

    return importlib.reload(A)


# =====================================================================
# Main API / gate / solver flow
# =====================================================================

def test_api_and_gate_flow(tmp_path, monkeypatch):
    """Verify the main API flow without human authentication."""

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:

        # =============================================================
        # Application status
        # =============================================================

        response = client.get("/api/status")

        assert response.status_code == 200

        status = response.json()

        assert status["air_gapped"] is True
        assert status["user"]["username"] == "guest"
        assert status["user"]["role"] == "supervisor"
        assert status["cloud_llm_allowed"] is False

        # =============================================================
        # Public monitoring endpoints
        # =============================================================

        health = client.get("/health")

        assert health.status_code == 200
        assert health.json()["status"] in ("ok", "degraded")

        metrics = client.get("/metrics")

        assert metrics.status_code == 200
        assert "sovereign_http_requests_total" in metrics.text

        # =============================================================
        # Authentication compatibility
        # =============================================================

        # GET login is not supported.
        assert client.get("/api/auth/login").status_code == 404

        # Login is a compatibility endpoint.
        login_response = client.post(
            "/api/auth/login",
            json={
                "username": "anything",
                "pin": "anything",
            },
        )

        assert login_response.status_code == 200

        login_data = login_response.json()

        assert login_data["username"] == "guest"
        assert login_data["role"] == "supervisor"

        # Session endpoint returns anonymous compatibility identity.
        me_response = client.get("/api/auth/me")

        assert me_response.status_code == 200

        me_data = me_response.json()

        assert me_data["username"] == "guest"
        assert me_data["role"] == "supervisor"

        # Logout is a no-op compatibility endpoint.
        logout_response = client.post("/api/auth/logout")

        assert logout_response.status_code == 200

        # =============================================================
        # User management is removed
        # =============================================================

        assert client.get("/api/users").status_code == 404

        assert client.post(
            "/api/users",
            json={
                "username": "demo-user",
                "pin": "123456",
                "role": "operator",
            },
        ).status_code == 405

        # =============================================================
        # SKU-2 cloud LLM protection
        # =============================================================

        response = client.post(
            "/api/settings",
            json={
                "nl_mode": "cloud-llm",
            },
        )

        assert response.status_code == 403
        assert "SKU-2" in response.json()["detail"]

        # =============================================================
        # NL proposal
        # =============================================================

        response = client.post(
            "/api/nl/propose",
            json={
                "text": "Tank 3 sulfur limit <= 0.3%",
            },
        )

        assert response.status_code == 200

        proposal = response.json()

        assert proposal["status"] == "pending_signoff"
        assert proposal["required_role"] == "supervisor"
        assert proposal["operator"] == "guest"

        # =============================================================
        # NL proposal approval
        # =============================================================

        response = client.post(
            f"/api/nl/proposals/{proposal['id']}/decide",
            json={
                "action": "approve",
                "note": "PSE ok",
            },
        )

        assert response.status_code == 200

        decision = response.json()

        assert decision["decided_by"] == "guest"
        assert decision["stage"] == "shadow"

        # =============================================================
        # Plan solving
        # =============================================================

        response = client.post("/api/plan/solve")

        assert response.status_code == 200

        plan = response.json()

        assert plan["production"]["status"] == "optimal"
        assert plan["shadow"]["status"] == "optimal"

        # =============================================================
        # Race WebSocket
        # =============================================================

        with client.websocket_connect("/ws/race") as websocket:

            websocket.send_json(
                {
                    "model": "refinery",
                }
            )

            events = []

            while True:
                message = websocket.receive_json()
                event_type = message["type"]

                events.append(event_type)

                if event_type == "verdict":
                    assert message["agrees"] is True

                if event_type in ("done", "error"):
                    break

        assert "iter" in events
        assert events[-1] == "done"

        # =============================================================
        # Reoptimization
        # =============================================================

        response = client.post(
            "/api/plan/reoptimize",
            json={
                "product_prices": {
                    "HSD": 115,
                }
            },
        )

        assert response.status_code == 200

        reoptimization = response.json()

        assert reoptimization["agree"] is True
        assert reoptimization["warm"]["start"] == "warm"

        # =============================================================
        # Invalid MPS upload
        # =============================================================

        response = client.post(
            "/api/models/mps",
            json={
                "name": "x.mps",
                "text": "ROWS\n N C\n Q X\n",
            },
        )

        assert response.status_code == 400

        # =============================================================
        # Oversized request
        # =============================================================

        response = client.post(
            "/api/models/mps",
            content=b"{}",
            headers={
                "Content-Length": str(10**9),
            },
        )

        assert response.status_code in (413, 422)

        # =============================================================
        # Audit
        # =============================================================

        response = client.get("/api/audit")

        assert response.status_code == 200

        audit = response.json()

        assert audit["verify"]["ok"] is True

        events = {
            entry["event"]
            for entry in audit["entries"]
        }

        assert {
            "nl.proposed",
            "nl.approved",
            "plan.solved",
            "race.completed",
        } <= events

        # =============================================================
        # Application + API documentation
        # =============================================================

        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 200


# =====================================================================
# WebSocket without authentication
# =====================================================================
def test_websocket_does_not_require_session(tmp_path, monkeypatch):
    """Verify WebSocket access without authentication."""

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:

        with client.websocket_connect("/ws/race") as websocket:

            websocket.send_json(
                {
                    "model": "refinery",
                }
            )

            verdict_received = False
            done_received = False

            while True:
                message = websocket.receive_json()

                event_type = message.get("type")

                if event_type == "verdict":
                    verdict_received = True
                    assert message["agrees"] is True

                elif event_type == "error":
                    pytest.fail(
                        f"Race WebSocket failed unexpectedly: {message}"
                    )

                elif event_type == "done":
                    done_received = True
                    break

            assert verdict_received is True
            assert done_received is True

# =====================================================================
# Authentication compatibility behavior
# =====================================================================

def test_authentication_compatibility_endpoint(tmp_path, monkeypatch):
    """
    Verify that real authentication is disabled while the legacy
    authentication endpoints remain harmless compatibility endpoints.
    """

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:

        # GET login is not supported.
        assert client.get("/api/auth/login").status_code == 404

        # Login ignores supplied credentials.
        response = client.post(
            "/api/auth/login",
            json={
                "username": "random-user",
                "pin": "incorrect-pin",
            },
        )

        assert response.status_code == 200

        data = response.json()

        assert data["username"] == "guest"
        assert data["role"] == "supervisor"

        # Session endpoint returns anonymous identity.
        response = client.get("/api/auth/me")

        assert response.status_code == 200

        data = response.json()

        assert data["username"] == "guest"
        assert data["role"] == "supervisor"

        # Logout is a no-op.
        response = client.post("/api/auth/logout")

        assert response.status_code == 200


# =====================================================================
# User-management endpoints
# =====================================================================

def test_user_management_endpoints_removed(tmp_path, monkeypatch):
    """Verify that user-management endpoints are not available."""

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:

        # GET route is absent.
        response = client.get("/api/users")

        assert response.status_code == 404

        # POST is not an allowed operation.
        response = client.post(
            "/api/users",
            json={
                "username": "demo-user",
                "pin": "123456",
                "role": "operator",
            },
        )

        assert response.status_code == 405


# =====================================================================
# Concurrent anonymous WebSocket clients
# =====================================================================

def test_concurrent_race_clients(tmp_path, monkeypatch):
    """Verify multiple anonymous WebSocket clients can run concurrently."""

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:

        results = []
        errors = []

        def run_client():
            try:
                with client.websocket_connect("/ws/race") as websocket:

                    websocket.send_json(
                        {
                            "model": "random-s",
                            "baselines": ["highs"],
                        }
                    )

                    while True:
                        message = websocket.receive_json()

                        if message["type"] == "verdict":
                            results.append(message["agrees"])

                        if message["type"] in ("done", "error"):
                            break

            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=run_client)
            for _ in range(4)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=120)

        assert not errors
        assert results == [True] * 4

        audit = client.get("/api/audit")

        assert audit.status_code == 200
        assert audit.json()["verify"]["ok"] is True


# =====================================================================
# Configuration validation
# =====================================================================

def test_config_fails_fast():
    """Verify important configuration validation."""

    from sovereign import config

    # SKU-2 must reject cloud LLM.
    with pytest.raises(
        ValueError,
        match="SKU-2",
    ):
        config.load(
            {
                "SOVEREIGN_SKU": "SKU-2",
                "SOVEREIGN_ALLOW_CLOUD_LLM": "1",
            }
        )

    # Invalid alert threshold must fail.
    with pytest.raises(ValueError):
        config.load(
            {
                "SOVEREIGN_ALERT_THRESHOLD": "zero",
            }
        )

    # Unknown environment variables are reported.
    settings = config.load(
        {
            "SOVEREIGN_SKU": "sku-1",
            "SOVEREIGN_ALLOW_CLOUD_LLM": "1",
            "SOVEREIGN_TYPO": "x",
        }
    )

    assert settings.cloud_allowed is True
    assert settings.unknown_env == ["SOVEREIGN_TYPO"]