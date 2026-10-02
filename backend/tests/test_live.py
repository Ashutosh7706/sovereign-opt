"""Live WebSocket API tests for the no-login Sovereign Optimizer build."""

import importlib

from fastapi.testclient import TestClient


def boot(tmp_path, monkeypatch):
    monkeypatch.setenv("SOVEREIGN_DATA", str(tmp_path))
    monkeypatch.setenv("SOVEREIGN_LOG_FORMAT", "text")

    import app as A

    return importlib.reload(A)


def collect_until_done(ws, max_messages=5000):
    """Collect WebSocket messages until the race sends its final `done` event."""
    messages = []

    for _ in range(max_messages):
        message = ws.receive_json()
        messages.append(message)

        if message.get("type") in ("done", "error"):
            break

    return messages


def test_live_race_without_login(tmp_path, monkeypatch):
    """Race WebSocket must work without login/session authentication."""

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:
        # No login call.
        # No session cookie.
        # No CSRF token.
        with client.websocket_connect("/ws/race") as ws:
            ws.send_json(
                {
                    "model": "random-s",
                    "device": "cpu",
                    "baselines": ["highs"],
                }
            )

            messages = collect_until_done(ws)

    assert messages
    assert messages[-1]["type"] == "done"

    types = {message["type"] for message in messages}

    assert "model" in types
    assert "lane_start" in types
    assert "lane_done" in types
    assert "verdict" in types
    assert "done" in types

    verdict = next(
        message
        for message in messages
        if message["type"] == "verdict"
    )

    assert verdict["agrees"] is True


def test_live_refinery_cpu_mode_without_login(tmp_path, monkeypatch):
    """
    Refinery race must work without authentication and must preserve
    CPU-only mode.
    """

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:
        with client.websocket_connect("/ws/race") as ws:
            ws.send_json(
                {
                    "model": "refinery",
                    "device": "cpu",
                    "baselines": ["highs", "highs_ipm"],
                }
            )

            messages = collect_until_done(ws)

    assert messages[-1]["type"] == "done"

    model = next(
        message
        for message in messages
        if message["type"] == "model"
    )

    assert model["gpu_mode"] == "off"

    sovereign = next(
        message
        for message in messages
        if message["type"] == "lane_done"
        and message["lane"] == "sovereign"
    )

    assert sovereign["device"] == "cpu"
    assert sovereign["status"] in (
        "optimal",
        "feasible",
    )

    ipm = next(
        message
        for message in messages
        if message["type"] == "lane_done"
        and message["lane"] == "highs_ipm"
    )

    # Refinery is a MIP, so HiGHS-IPM is not applicable.
    assert ipm["status"] == "not applicable"


def test_live_stream_contains_solver_progress(tmp_path, monkeypatch):
    """Verify that the live race still streams solver progress."""

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:
        with client.websocket_connect("/ws/race") as ws:
            ws.send_json(
                {
                    "model": "random-s",
                    "device": "cpu",
                    "baselines": ["highs"],
                }
            )

            messages = collect_until_done(ws)

    iterations = [
        message
        for message in messages
        if message["type"] == "iter"
    ]

    nodes = [
        message
        for message in messages
        if message["type"] == "node"
    ]

    lane_done = next(
        message
        for message in messages
        if message["type"] == "lane_done"
        and message["lane"] == "sovereign"
    )

    assert lane_done["status"] in (
        "optimal",
        "feasible",
    )

    assert lane_done["device"] == "cpu"

    # At least one live-progress stream should normally be produced.
    assert iterations or nodes


def test_twin_websocket_without_login(tmp_path, monkeypatch):
    """Digital-twin WebSocket must also work without authentication."""

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:
        with client.websocket_connect("/ws/twin") as ws:
            ws.send_json({"stage": "production"})
            messages = []

            for _ in range(5000):
                message = ws.receive_json()
                messages.append(message)

                if message.get("type") in ("final", "error"):
                    break

    assert messages

    # Authentication is not required, so the socket should not
    # immediately terminate with an authentication error.
    first = messages[0]

    assert first.get("type") != "auth_error"
    assert first.get("type") != "login_required"


def test_multiple_live_clients_without_login(tmp_path, monkeypatch):
    """Multiple clients can open live WebSockets without sessions."""

    A = boot(tmp_path, monkeypatch)

    with TestClient(A.app) as client:
        with client.websocket_connect("/ws/race") as ws1:
            ws1.send_json(
                {
                    "model": "random-s",
                    "device": "cpu",
                    "baselines": ["highs"],
                }
            )

            messages1 = collect_until_done(ws1)

        with client.websocket_connect("/ws/race") as ws2:
            ws2.send_json(
                {
                    "model": "random-s",
                    "device": "cpu",
                    "baselines": ["highs"],
                }
            )

            messages2 = collect_until_done(ws2)

    assert messages1[-1]["type"] == "done"
    assert messages2[-1]["type"] == "done"

    verdict1 = next(
        m for m in messages1 if m["type"] == "verdict"
    )

    verdict2 = next(
        m for m in messages2 if m["type"] == "verdict"
    )

    assert verdict1["agrees"] is True
    assert verdict2["agrees"] is True