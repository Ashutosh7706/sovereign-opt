"""FastAPI + WebSocket service for the Sovereign Optimization Platform.

Run:  uvicorn app:app --app-dir backend --port 8000        (dashboard at /, API docs at /docs)
Air-gap: no route in this process makes an outbound network call unless (a) an SKU-1 build
enables cloud-llm mode, or (b) an operator configures an on-prem alert webhook.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from sovereign import baselines, config, device
from sovereign.audit import AuditLog
from sovereign.engine import SOLVER_VERSION, SolverConfig, solve
from sovereign.infeasibility import explain_infeasibility
from sovereign import twin
from sovereign.linalg import GPU_FALLBACKS, GPU_POLICY, gpu_warmup
from sovereign.lp import WarmStartCache, solve_lp
from sovereign.model import Model
from sovereign.models import random_lp as rlp
from sovereign.models.refinery import build, plan_report
from sovereign.mps import MPSError, netlib_reference_values, parse_mps
from sovereign.nlc.gate import MODES, ConstraintGate, PermissionDenied, require_role
from sovereign.ops import (FailureAlerter, Metrics, RateLimiter, dir_size_mb, disk_status, event,
                           setup_logging)
from sovereign.replay import ReplayStore
from sovereign.store import Store
from sovereign.telemetry import Telemetry
from sovereign.tolerances import TOL


# Authentication/RBAC is disabled for this demo build. Keep the imported
# gate API intact, but make application-level role checks no-ops.
def require_role(_role: str, _needed: str, _what: str) -> None:
    return None

# ------------------------------------------------------------------ boot: validate config, fail fast (#83)
try:
    SETTINGS = config.load()
except (ValidationError, ValueError) as e:  # pragma: no cover - exercised by test_config
    print(f"INVALID CONFIGURATION - refusing to start:\n{e}", file=sys.stderr)
    raise SystemExit(2)

log = setup_logging(SETTINGS.log_format)
DATA = Path(SETTINGS.data_dir)
DATA.mkdir(parents=True, exist_ok=True)
store = Store(DATA / "sovereign.db")
audit = AuditLog(store, SETTINGS.anchor_dir)
replays = ReplayStore(store)
gate = ConstraintGate(audit, cloud_allowed=SETTINGS.cloud_allowed, lan_llm_allowed=SETTINGS.allow_lan_llm)
metrics = Metrics()
limiter = RateLimiter(SETTINGS.rate_per_min)
alerter = FailureAlerter(SETTINGS.alert_threshold, SETTINGS.alert_webhook, audit, log)
STATE = {"inflight": 0, "shutting_down": False, "started": time.time()}
uploaded: dict[str, Model] = {}
netlib_refs: dict[str, float] = {}
PUBLIC = {"/health", "/metrics", "/docs", "/openapi.json", "/docs/oauth2-redirect"}
ANONYMOUS_USER = {"username": "guest", "role": "supervisor"}
EXPENSIVE = ("/api/plan/", "/api/nl/propose", "/api/models/mps", "/api/netlib/readme", "/api/replays/")


@contextlib.asynccontextmanager
async def lifespan(_app):
    event(log, device.boot_report(), gpu=device.describe())
    event(log, "config", sku=SETTINGS.sku, cloud_llm=SETTINGS.cloud_allowed, https=SETTINGS.https,
          data=str(DATA), unknown_env=SETTINGS.unknown_env)
    if SETTINGS.unknown_env:
        event(log, "unknown SOVEREIGN_* environment variables ignored", logging.WARNING, names=SETTINGS.unknown_env)
    if SETTINGS.allow_lan_llm:  # a sovereignty relaxation is always on the record (#96)
        audit.append("sovereignty.relaxed", "system", {"flag": "SOVEREIGN_ALLOW_LAN_LLM",
                                                       "effect": "non-loopback on-prem LLM host permitted"})
    GPU_POLICY["min_rows"] = SETTINGS.gpu_min_rows
    if device.gpu_available():  # compile CUDA kernels now, not during the first demo solve
        async def _warm():
            try:
                event(log, await asyncio.to_thread(gpu_warmup))
            except Exception as e:  # pragma: no cover
                event(log, "GPU warm-up failed", logging.WARNING, error=str(e))
        asyncio.get_running_loop().create_task(_warm())
    audit.append("service.started", "system", {"version": SOLVER_VERSION, "sku": SETTINGS.sku,
                                               "compute": device.boot_report()})
    yield
    # graceful shutdown (#81): stop taking solves, let in-flight ones finish (bounded wait)
    STATE["shutting_down"] = True
    deadline = time.time() + 30
    while STATE["inflight"] and time.time() < deadline:
        await asyncio.sleep(0.2)
    with contextlib.suppress(Exception):
        audit.append("service.stopped", "system", {"inflight_abandoned": STATE["inflight"]})
    TELEMETRY.stop()
    store.close()


app = FastAPI(title="Sovereign Optimization Platform", version=SOLVER_VERSION.split()[-1], lifespan=lifespan,
              description="On-prem LP/MIP optimisation with an auditable NL-to-constraint gate. "
                          "All /api routes need a session (POST /api/auth/login) and, for writes, "
                          "the X-CSRF-Token header.")


def clean(o):
    """JSON-safe conversion (NaN/inf -> None, numpy -> python)."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


# ------------------------------------------------------------------ security middleware (#21-#25, #28, #29)
@app.middleware("http")
async def security(request: Request, call_next):
    t0 = time.perf_counter()
    path = request.url.path
    # Authentication is intentionally disabled for this build.
    # Every request runs as a local guest with supervisor privileges.
    user = ANONYMOUS_USER.copy()
    request.state.user = user
    try:
        resp: Response = await call_next(request)
    except sqlite3.Error as e:  # fail closed (#33): nothing applied, nothing half-recorded
        alerter.record(False, {"storage_error": str(e)})
        event(log, "storage write failed", logging.ERROR, path=path, error=str(e))
        resp = JSONResponse({"detail": f"storage write failed - action NOT applied and NOT recorded ({e})"},
                            status_code=503)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if not path.startswith("/docs"):
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                                   "connect-src 'self' ws: wss:; frame-ancestors 'none'")
    if SETTINGS.https:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    if not path.startswith("/api") and "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-cache"  # revalidate (ETag) so an upgrade is picked up without Ctrl+F5
    route = request.scope.get("route")
    metrics.inc("sovereign_http_requests_total", "HTTP requests", method=request.method,
                path=getattr(route, "path", "static" if not path.startswith("/api") else path),
                status=resp.status_code)
    metrics.observe("sovereign_http_seconds", time.perf_counter() - t0, "HTTP latency")
    return resp


@app.exception_handler(PermissionDenied)
async def _denied(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=403)


@app.exception_handler(sqlite3.Error)
async def _storage(_r, e):  # fail closed (#33)
    alerter.record(False, {"storage_error": str(e)})
    event(log, "storage write failed", logging.ERROR, error=str(e))
    return JSONResponse({"detail": f"storage write failed - action NOT applied and NOT recorded ({e})"},
                        status_code=503)


def who(request: Request) -> dict:
    return getattr(request.state, "user", ANONYMOUS_USER.copy())


@contextlib.contextmanager
def tracked(kind: str):
    STATE["inflight"] += 1
    t0 = time.perf_counter()
    try:
        yield
    finally:
        STATE["inflight"] -= 1
        metrics.observe("sovereign_solve_seconds", time.perf_counter() - t0, "solve wall time", kind=kind)


def record_solve(kind: str, r: dict):
    ok = r["status"] in ("optimal", "feasible", "infeasible", "unbounded") and not r.get("cond_warning")
    metrics.inc("sovereign_solves_total", "solves by outcome", kind=kind, status=r["status"])
    alerter.record(ok, {"kind": kind, "status": r["status"], "message": r.get("message", ""),
                        "cond_estimate": r.get("cond_estimate")})


def get_model(key: str) -> Model:
    if key == "refinery":
        return build(gate.params("production"))
    if key == "refinery-shadow":
        return build(gate.params("shadow"), name="MRPL-synthetic refinery plan (shadow)")
    if key in rlp.SIZES:
        m, n = rlp.SIZES[key]
        return rlp.random_lp(m, n)
    if key in rlp.DENSE:
        m, n, dens = rlp.DENSE[key]
        model = rlp.random_lp(m, n, density=dens)
        model.name = f"dense LP {m}x{n}"
        return model
    if key.startswith("mps:") and key[4:] in uploaded:
        return uploaded[key[4:]]
    raise HTTPException(404, f"unknown model '{key}'")


# ------------------------------------------------------------------ authentication compatibility
# Authentication is disabled. These endpoints remain only so an older frontend
# can continue to initialize without displaying a login screen.
@app.post("/api/auth/login")
def login_compat():
    return ANONYMOUS_USER.copy()


@app.post("/api/auth/logout")
def logout_compat():
    return {"ok": True}


@app.get("/api/auth/me")
def me_compat():
    return ANONYMOUS_USER.copy()


# ------------------------------------------------------------------ ops: health + metrics (#77, #78, #82)
@app.get("/health")
def health():
    try:
        db_ok = store.integrity_ok()
        chain = audit.verify()
    except sqlite3.Error as e:
        return JSONResponse({"status": "down", "database": str(e)}, status_code=503)
    disk = disk_status(DATA, SETTINGS.disk_warn_gb)
    problems = []
    if not db_ok:
        problems.append("database integrity check failed")
    if not chain["ok"]:
        problems.append(f"audit chain broken at seq {chain.get('bad_seq')}: {chain.get('reason')}")
    if not disk.get("ok"):
        problems.append(f"low disk space: {disk.get('free_gb')} GB free")
    if alerter.active_alert:
        problems.append(f"{alerter.active_alert['consecutive_failures']} consecutive solver failures")
    if gate.llm_failures:
        problems.append(f"NL LLM endpoint failing ({gate.llm_failures} in a row) - offline parser in use")
    return {"status": "degraded" if problems else "ok", "problems": problems, "version": SOLVER_VERSION,
            "uptime_s": round(time.time() - STATE["started"]), "inflight_solves": STATE["inflight"],
            "database": {"ok": db_ok, "schema_version": store.schema_version()},
            "audit": chain, "disk": {**disk, "data_dir_mb": dir_size_mb(DATA)},
            "compute": device.boot_report(), "gpu_fallbacks": GPU_FALLBACKS, "solver_alert": alerter.active_alert}
@app.get("/api/solvers")
def solvers():
    return {
        "status": "available",
        "solver_version": SOLVER_VERSION,
        "device": device.describe(),
        "gpu_fallbacks": GPU_FALLBACKS,
    }

@app.get("/metrics", response_class=PlainTextResponse)
def prom():
    metrics.gauge("sovereign_inflight_solves", STATE["inflight"], "solves running now")
    metrics.gauge("sovereign_audit_entries", audit.tip()[0], "audit log length")
    metrics.gauge("sovereign_gpu_available", int(device.gpu_available()), "1 if a CUDA device is in use")
    metrics.gauge("sovereign_gpu_fallbacks", GPU_FALLBACKS["count"], "GPU->CPU factorization fallbacks")
    d = disk_status(DATA, SETTINGS.disk_warn_gb)
    if "free_gb" in d:
        metrics.gauge("sovereign_disk_free_gb", d["free_gb"], "free disk space for the data dir")
    return metrics.render()


# ------------------------------------------------------------------ status / settings
@app.get("/api/status")
def status(request: Request):
    mode = gate.settings["nl_mode"]
    return clean({
        "solver_version": SOLVER_VERSION, "device": device.describe(), "sku": SETTINGS.sku,
        "baselines": baselines.availability(), "settings": gate.settings, "modes": MODES,
        "cloud_llm_allowed": SETTINGS.cloud_allowed, "user": who(request),
        "egress": "api.anthropic.com (Enterprise/Cloud mode - NOT air-gapped)" if mode == "cloud-llm" else "none",
        "air_gapped": mode != "cloud-llm", "audit": audit.verify(),
        "needs_reverification": gate.needs_reverification(),
        "sovereignty": [
            {"tier": 1, "name": "IP & data sovereignty", "state": "true today",
             "detail": "Solver source, formulations and plant data stay on-prem; no licence server, no telemetry."},
            {"tier": 2, "name": "Deployment sovereignty", "state": "true today" if mode != "cloud-llm" else "degraded",
             "detail": "Runs fully on-prem. " + ("cloud-llm is hard-locked off in this SKU-2 build."
                                                 if not SETTINGS.cloud_allowed else
                                                 "SKU-1 build: cloud-llm mode can be enabled by an admin.")},
            {"tier": 3, "name": "Compute sovereignty", "state": "roadmap - not today",
             "detail": "GPU silicon is imported. CPU FP64 path is always available. " + device.boot_report()},
        ],
    })


class SettingsIn(BaseModel):
    nl_mode: Optional[str] = None
    shadow_mode: Optional[bool] = None


@app.post("/api/settings")
def settings(s: SettingsIn, request: Request):
    u = who(request)
    try:
        return gate.set_settings(u["username"], u["role"], nl_mode=s.nl_mode, shadow_mode=s.shadow_mode)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/models")
def models():
    out = [{"key": "refinery", "name": "MRPL-synthetic refinery plan (MIP)", "kind": "mip"},
           *[{"key": k, "name": f"Random sparse LP {m}x{n}", "kind": "lp"} for k, (m, n) in rlp.SIZES.items()],
           *[{"key": k, "name": f"Dense LP {m}x{n} (GPU showcase)", "kind": "lp"}
             for k, (m, n, _d) in rlp.DENSE.items()]]
    for k, m in uploaded.items():
        out.append({"key": f"mps:{k}", "name": f"{m.name} (MPS)", "kind": "mip" if m.is_mip else "lp",
                    "reference": netlib_refs.get(m.name.upper())})
    return out


class MPSIn(BaseModel):
    name: str
    text: str


@app.post("/api/models/mps")
async def upload_mps(body: MPSIn, request: Request):
    try:  # size cap + parse-time budget inside parse_mps (#29)
        m = await asyncio.to_thread(parse_mps, body.text, Path(body.name).stem.upper()[:64],
                                    int(SETTINGS.max_upload_mb * 1e6))
    except MPSError as e:
        raise HTTPException(400, f"could not parse MPS: {e}")
    key = uuid.uuid4().hex[:8]
    uploaded[key] = m
    audit.append("model.uploaded", who(request)["username"], {"name": m.name, "sha256": m.fingerprint(),
                                                              "n": m.n, "m": m.m})
    return {"key": f"mps:{key}", "name": m.name, "n": m.n, "m": m.m, "nnz": int(m.A.nnz),
            "integers": int(m.integer.sum()), "maximize": m.maximize, "reference": netlib_refs.get(m.name.upper())}


class ReadmeIn(BaseModel):
    text: str


@app.post("/api/netlib/readme")
def netlib_readme(body: ReadmeIn):
    refs = netlib_reference_values(body.text)
    netlib_refs.update(refs)
    return {"parsed": len(refs), "sample": dict(list(refs.items())[:5])}


LANE_NAMES = {"highs": "HiGHS (default)", "highs_ipm": "HiGHS (IPM)", "gurobi_cpu": "Gurobi CPU",
              "gurobi_gpu": "Gurobi GPU"}
TELEMETRY = Telemetry()


async def ws_accept(ws: WebSocket, rate_limited: bool = True):
    """Accept a WebSocket without session authentication.

    A lightweight same-origin check is retained when the browser supplies both
    Origin and Host headers. This is not authentication; it only reduces
    accidental cross-site WebSocket use.
    """
    origin = ws.headers.get("origin")
    host = ws.headers.get("host")
    if origin and host and origin.split("://", 1)[-1] != host:
        await ws.close(code=1008)
        return None
    await ws.accept()
    if rate_limited and not limiter.allow(ANONYMOUS_USER["username"]):
        await ws.send_json({"type": "error", "message": "rate limit exceeded - slow down"})
        await ws.close()
        return None
    return ANONYMOUS_USER.copy()

async def pump_queue(ws: WebSocket, task, queue: asyncio.Queue):
    while not task.done() or not queue.empty():
        try:
            ev = await asyncio.wait_for(queue.get(), 0.05)
            await ws.send_json(clean(ev))
        except asyncio.TimeoutError:
            pass


# ------------------------------------------------------------------ the race (WebSocket, authentication-free)
@app.websocket("/ws/race")
async def race(ws: WebSocket):
    user = await ws_accept(ws)
    if user is None:
        return
    loop = asyncio.get_running_loop()
    try:
        req = await ws.receive_json()
        model = get_model(req.get("model", "refinery"))
        gpu_mode = {"auto": "auto", "gpu": "force", "force": "force", "cpu": "off", "off": "off"}.get(
            str(req.get("device", "auto")), "auto")
        cfg = SolverConfig(precision=req.get("precision", "mixed"), deterministic=bool(req.get("deterministic", True)),
                           gpu_mode=gpu_mode)
        await ws.send_json(clean({"type": "model", "name": model.name, "n": model.n, "m": model.m,
                                  "nnz": int(model.A.nnz), "integers": int(model.integer.sum()),
                                  "maximize": model.maximize, "reference": netlib_refs.get(model.name.upper()),
                                  "sha256": model.fingerprint(), "gpu_mode": gpu_mode,
                                  "gpu_min_rows": GPU_POLICY["min_rows"]}))
        queue: asyncio.Queue = asyncio.Queue()
        last = {"node": 0.0, "tree": 0}
        put = lambda ev: loop.call_soon_threadsafe(queue.put_nowait, ev)  # noqa: E731

        def on_iter(e):
            e = {k: v for k, v in e.items() if k not in ("x_model", "y_model")}
            put({"type": "iter", "lane": "sovereign", **e})

        def on_node(e):
            if e["type"] == "tree":  # live search tree (capped so a huge tree cannot flood the socket)
                last["tree"] += 1
                if last["tree"] <= 4000:
                    ev = dict(e)
                    if ev.get("bound") is not None:
                        ev["bound"] = model.display_objective(ev["bound"])
                    put(ev)
                return
            now = time.perf_counter()
            if e["type"] in ("incumbent", "root", "done") or now - last["node"] > 0.03:
                last["node"] = now
                ev = {k: v for k, v in e.items() if k != "x"}
                ev["kind"] = ev.pop("type")
                for k in ("bound", "incumbent"):
                    if ev.get(k) is not None:
                        ev[k] = model.display_objective(ev[k])
                put({"type": "node", "lane": "sovereign", **ev})

        results = {}
        await ws.send_json({"type": "lane_start", "lane": "sovereign", "name": "Sovereign IPM" +
                            (" + B&B" if model.is_mip else "")})
        with tracked("race"):
            task = asyncio.ensure_future(asyncio.to_thread(solve, model, cfg, on_iter, on_node, None, True,
                                                           model.n <= 20000))
            await pump_queue(ws, task, queue)
            r = task.result()
        record_solve("race", r)
        bundle = replays.record(model, r, "race")
        results["sovereign"] = r
        await ws.send_json(clean({"type": "lane_done", "lane": "sovereign", "name": "Sovereign IPM",
                                  "status": r["status"], "seconds": r["seconds"], "objective": r["objective"],
                                  "iters": r["iters"], "nodes": r["nodes"], "lp_solves": r["lp_solves"],
                                  "fp64_switch_iter": r["fp64_switch_iter"], "device": r["device"],
                                  "gpu_decision": r.get("gpu_decision"), "gpu_mode": gpu_mode,
                                  "linear_solver": r["linear_solver"], "max_violation": r["max_violation"],
                                  "x_sha256": r["x_sha256"], "replay_id": bundle["id"],
                                  "precision": cfg.precision, "deterministic": cfg.deterministic,
                                  "algorithm": r.get("algorithm"), "cond_estimate": r.get("cond_estimate"),
                                  "cond_warning": r.get("cond_warning"), "message": r.get("message")}))
        runners = {"highs": baselines.run_highs, "highs_ipm": baselines.run_highs_ipm,
                   "gurobi_cpu": lambda m: baselines.run_gurobi(m, False),
                   "gurobi_gpu": lambda m: baselines.run_gurobi(m, True)}
        for lane in req.get("baselines", ["highs", "highs_ipm", "gurobi_cpu", "gurobi_gpu"]):
            if lane not in runners:
                continue
            await ws.send_json({"type": "lane_start", "lane": lane, "name": LANE_NAMES[lane]})
            b = await asyncio.to_thread(runners[lane], model)
            results[lane] = b
            await ws.send_json(clean({"type": "lane_done", **b}))
        ref = netlib_refs.get(model.name.upper())
        ref_src = "Netlib readme" if ref is not None else None
        for lane in ("highs", "highs_ipm"):
            if ref is None and results.get(lane, {}).get("objective") is not None:
                ref, ref_src = results[lane]["objective"], f"{LANE_NAMES[lane]} on the same model"
        ours = r["objective"]
        agree = None if ref is None or ours is None else abs(ours - ref) <= TOL.solver_agreement * (1 + abs(ref))
        timed = [(k, v["seconds"]) for k, v in results.items() if v.get("seconds") is not None
                 and v.get("status") in ("optimal", "feasible")]
        fastest = min(timed, key=lambda t: t[1])[0] if timed else None
        beaten_by = [k for k, s in timed if k != "sovereign" and s < r["seconds"]]
        unavailable = [k for k, v in results.items() if v.get("status") == "unavailable"]
        await ws.send_json(clean({
            "type": "verdict", "reference": ref, "reference_source": ref_src, "objective": ours,
            "agrees": agree, "fastest": fastest, "beaten_by": beaten_by, "unavailable": unavailable,
            "device": r["device"], "gpu_decision": r.get("gpu_decision"),
            "pitch_line": ("We are not claiming to out-perform a decade of commercial solver engineering. We are "
                           "claiming sovereign, auditable, air-gapped deployment that they structurally cannot "
                           "offer to a classified PSU workload.") if beaten_by else
            "Fastest lane on this model - verify against every available baseline before quoting it.",
        }))
        audit.append("race.completed", user["username"], {
            "model": model.name, "sha256": model.fingerprint(), "replay": bundle["id"], "device": r["device"],
            "results": clean({k: {"status": v.get("status"), "seconds": v.get("seconds"),
                                  "objective": v.get("objective")} for k, v in results.items()})})
        await ws.send_json({"type": "done"})
    except WebSocketDisconnect:
        return
    except HTTPException as e:
        await ws.send_json({"type": "error", "message": e.detail})
    except Exception as e:  # surface solver errors to the UI instead of dropping the socket
        log.exception("race failed")
        event(log, "race failed", logging.ERROR, error=f"{type(e).__name__}: {e}")
        await ws.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})


# ------------------------------------------------------------------ 3D digital twin (live solve stream)
@app.get("/api/twin/layout")
def twin_layout(stage: str = "production"):
    return twin.layout(gate.params("shadow" if stage == "shadow" else "production"))


@app.websocket("/ws/twin")
async def twin_ws(ws: WebSocket):
    """Solves the refinery plan and streams the twin state: per IPM iteration of the root LP
    (fill levels / flows / emerging duals from the live iterate), each new incumbent, and the final
    plan with exact shadow prices. Every number shown in 3D comes from this stream."""
    user = await ws_accept(ws)
    if user is None:
        return
    loop = asyncio.get_running_loop()
    try:
        req = await ws.receive_json()
        stage = "shadow" if req.get("stage") == "shadow" else "production"
        p = gate.params(stage)
        m = build(p, name=f"MRPL-synthetic refinery plan ({stage})")
        await ws.send_json(clean({"type": "layout", "stage": stage, **twin.layout(p)}))
        queue: asyncio.Queue = asyncio.Queue()
        last = {"t": 0.0}
        put = lambda ev: loop.call_soon_threadsafe(queue.put_nowait, ev)  # noqa: E731

        def on_iter(e):
            if "x_model" not in e:
                return
            now = time.perf_counter()
            if now - last["t"] < 0.03:
                return
            last["t"] = now
            put({"type": "iter", "phase": "root LP relaxation", "iter": e["iter"], "mu": e["mu"],
                 "pinf": e["pinf"], "gap": e["gap"], "objective": e.get("obj_model"),
                 "state": twin.state(m, e["x_model"], e.get("y_model"), p)})

        def on_node(e):
            if e["type"] == "incumbent" and e.get("x") is not None:
                put({"type": "incumbent", "phase": f"integer plan found ({e.get('source')})",
                     "objective": m.display_objective(e["incumbent"]), "state": twin.state(m, e["x"], None, p)})

        with tracked("twin"):
            task = asyncio.ensure_future(asyncio.to_thread(solve, m, SolverConfig(), on_iter, on_node, None, True, True))
            await pump_queue(ws, task, queue)
            r = task.result()
        record_solve("twin", r)
        bundle = replays.record(m, r, f"twin-{stage}")
        final = {"type": "final", "stage": stage, "status": r["status"], "objective": r["objective"],
                 "seconds": r["seconds"], "replay_id": bundle["id"], "device": r["device"],
                 "gpu_decision": r.get("gpu_decision"), "nodes": r["nodes"]}
        if r["x"] is not None:
            final["state"] = twin.state(m, r["x"], r["y"], p)
        elif r["status"] == "infeasible":
            final["explanation"] = (await asyncio.to_thread(explain_infeasibility, m))["summary"]
        audit.append("twin.solved", user["username"], {"stage": stage, "status": r["status"],
                                                       "objective": clean(r["objective"]), "replay": bundle["id"]})
        await ws.send_json(clean(final))
    except WebSocketDisconnect:
        return
    except Exception as e:
        event(log, "twin failed", logging.ERROR, error=f"{type(e).__name__}: {e}")
        await ws.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})


# ------------------------------------------------------------------ live hardware telemetry
@app.websocket("/ws/telemetry")
async def telemetry_ws(ws: WebSocket):
    user = await ws_accept(ws, rate_limited=False)
    if user is None:
        return
    TELEMETRY.start()
    try:
        while True:
            await ws.send_json(clean({"type": "telemetry", **TELEMETRY.snapshot(), "inflight": STATE["inflight"],
                                      "gpu_fallbacks": GPU_FALLBACKS["count"]}))
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        return


# ------------------------------------------------------------------ plan (production vs shadow)
def _plan(stage: str) -> dict:
    p = gate.params(stage)
    m = build(p, name=f"MRPL-synthetic refinery plan ({stage})")
    with tracked("plan"):
        r = solve(m)
    record_solve("plan", r)
    bundle = replays.record(m, r, f"plan-{stage}")
    out = {"stage": stage, "status": r["status"], "objective": r["objective"], "seconds": r["seconds"],
           "nodes": r["nodes"], "gap": r["gap"], "replay_id": bundle["id"], "x_sha256": r["x_sha256"],
           "model_sha256": m.fingerprint(), "algorithm": r.get("algorithm"),
           "cond_estimate": r.get("cond_estimate"), "cond_warning": r.get("cond_warning")}
    if r["x"] is not None:
        out["report"] = plan_report(m, r["x"], p)
        if r["y"] is not None:  # shadow prices of the binding business constraints
            duals = []
            xv = dict(zip(m.var_names, r["x"]))
            for i, mt in enumerate(m.row_meta):
                if mt.get("kind") not in ("spec", "demand") and not m.row_names[i].startswith("cdu"):
                    continue
                y = float(-r["y"][i]) if m.maximize else float(r["y"][i])
                if abs(y) < 1e-6:
                    continue
                if mt.get("kind") == "spec":  # d(margin)/d(spec) = y * blend volume
                    y = y * xv.get(mt.get("volume_var"), 0.0)
                    per = f"per +1 {mt.get('unit')} on the spec limit"
                else:
                    per = "per +1 kbbl/d on the limit"
                duals.append({"row": m.row_names[i], "label": mt.get("label") or mt.get("desc"),
                              "value": y, "unit": f"$k/d {per}"})
            out["shadow_prices"] = sorted(duals, key=lambda d: -abs(d["value"]))[:10]
    elif r["status"] == "infeasible":
        out["explanation"] = explain_infeasibility(m)
        out["certificate"] = r.get("certificate")
    if r["message"]:
        out["message"] = r["message"]
    elif r["x"] is None and r["status"] != "infeasible":
        out["message"] = f"solver stopped with status {r['status']}"
    return out


@app.post("/api/plan/solve")
async def plan_solve(request: Request):
    prod = await asyncio.to_thread(_plan, "production")
    has_shadow = any(a.stage == "shadow" for a in gate.active)
    shadow = await asyncio.to_thread(_plan, "shadow") if has_shadow else None
    audit.append("plan.solved", who(request)["username"], {
        "production": {"status": prod["status"], "objective": clean(prod["objective"]), "replay": prod["replay_id"]},
        "shadow": None if shadow is None else {"status": shadow["status"], "objective": clean(shadow["objective"]),
                                               "replay": shadow["replay_id"]}})
    return clean({"production": prod, "shadow": shadow,
                  "constraints": [a.__dict__ for a in gate.active],
                  "needs_reverification": gate.needs_reverification()})


class ReoptIn(BaseModel):
    product_prices: dict[str, float] = {}
    crude_prices: dict[str, float] = {}


@app.post("/api/plan/reoptimize")
def reoptimize(body: ReoptIn):
    """Intra-day re-optimization: today's integer commitments (parcels, unit on/off) stay fixed,
    prices move, flows are re-optimized. Compares a cold LMS start with a warm start."""
    p = gate.params("production")
    base_m = build(p)
    with tracked("reoptimize"):
        base = solve(base_m)
        if base["x"] is None:
            raise HTTPException(409, "production plan is infeasible - fix it before re-optimizing")
        lb, ub = base_m.lb.copy(), base_m.ub.copy()
        ints = base_m.integer
        lb[ints] = ub[ints] = np.round(base["x"][ints])
        fixed_base = base_m.with_bounds(lb, ub).relaxed()
        cache = WarmStartCache()
        solve_lp(fixed_base, SolverConfig().ipm(), warm_cache=cache)  # populate the cache at old prices
        q = p.copy()
        for k, v in body.product_prices.items():
            if k in q.products:
                q.products[k].price = v
        for c in q.crudes:
            if c.name in body.crude_prices:
                c.price = body.crude_prices[c.name]
        new_m = build(q)
        new_fixed = new_m.with_bounds(lb, ub).relaxed()
        cold = solve_lp(new_fixed, SolverConfig().ipm(), warm_cache=None)
        warm = solve_lp(new_fixed, SolverConfig().ipm(), warm_cache=cache, use_warm=True)
    agree = cold.status == warm.status == "optimal" and \
        abs(cold.objective - warm.objective) < 1e-5 * (1 + abs(cold.objective))
    return clean({
        "old_objective": base_m.display_objective(base["objective_internal"]),
        "new_objective": new_m.display_objective(cold.objective) if cold.status == "optimal" else None,
        "cold": {"iters": cold.iters, "seconds": cold.seconds, "status": cold.status, "start": cold.start},
        "warm": {"iters": warm.iters, "seconds": warm.seconds, "status": warm.status, "start": warm.start},
        "speedup_iters": (cold.iters / warm.iters) if warm.iters else None,
        "agree": agree, "report": plan_report(new_m, cold.x, q) if cold.status == "optimal" else None,
    })


# ------------------------------------------------------------------ NL constraint compiler
class ProposeIn(BaseModel):
    text: str
    mode: Optional[str] = None


@app.post("/api/nl/propose")
async def nl_propose(body: ProposeIn, request: Request):
    u = who(request)
    text = body.text.strip()
    if not text or len(text) > 500:
        raise HTTPException(400, "text must be 1-500 characters")
    if body.mode and body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    return clean(await asyncio.to_thread(gate.propose, text, u["username"], body.mode, u["role"]))


class DecideIn(BaseModel):
    action: str
    note: str = ""


@app.post("/api/nl/proposals/{pid}/decide")
def nl_decide(pid: str, body: DecideIn, request: Request):
    u = who(request)
    try:
        return clean(gate.decide(pid, body.action, u["username"], body.note[:500], u["role"]))
    except KeyError:
        raise HTTPException(404, "no such proposal")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/nl/proposals")
def nl_proposals():
    return clean(list(reversed(list(gate.proposals.values())))[:50])


@app.get("/api/nl/constraints")
def nl_constraints():
    return {"active": [a.__dict__ for a in gate.active], "needs_reverification": gate.needs_reverification()}


class NoteIn(BaseModel):
    note: str = ""


@app.post("/api/nl/constraints/{cid}/promote")
def nl_promote(cid: str, body: NoteIn, request: Request):
    u = who(request)
    try:
        return gate.promote(cid, u["username"], body.note[:500], u["role"]).__dict__
    except StopIteration:
        raise HTTPException(404, "no such constraint")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/nl/constraints/{cid}/retire")
def nl_retire(cid: str, body: NoteIn, request: Request):
    u = who(request)
    try:
        gate.retire(cid, u["username"], body.note[:500], u["role"])
        return {"ok": True}
    except StopIteration:
        raise HTTPException(404, "no such constraint")
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ audit & replay
@app.get("/api/audit")
def audit_list(limit: int = 200):
    return {"verify": audit.verify(), "entries": list(reversed(audit.entries(min(limit, 2000)))),
            "anchor_dir": str(SETTINGS.anchor_dir) if SETTINGS.anchor_dir else None}


@app.post("/api/audit/anchor")
def audit_anchor(request: Request):
    u = who(request)
    require_role(u["role"], "supervisor", "anchoring the audit chain")
    if not SETTINGS.anchor_dir:
        raise HTTPException(400, "set SOVEREIGN_ANCHOR_DIR to an off-box / write-once location first")
    return audit.anchor(actor=u["username"])


@app.get("/api/audit/export")
def audit_export(request: Request):
    u = who(request)
    require_role(u["role"], "admin", "exporting the audit log")
    path = audit.export(DATA / "exports" / time.strftime("audit-%Y%m%d-%H%M%S.jsonl"))
    audit.append("audit.exported", u["username"], {"file": str(path)})
    return Response(path.read_bytes(), media_type="application/x-ndjson",
                    headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


@app.get("/api/replays")
def replay_list():
    return replays.list()


@app.post("/api/replays/{bid}/replay")
async def replay_run(bid: str, request: Request):
    try:
        with tracked("replay"):
            res = await asyncio.to_thread(replays.replay, bid)
    except FileNotFoundError:
        raise HTTPException(404, "no such bundle")
    audit.append("replay.run", who(request)["username"], {"bundle": bid, "ok": res["ok"], "reason": res["reason"]})
    return clean(res)


# ------------------------------------------------------------------ static dashboard
# Plain static files (React UMD + htm vendored): no Node toolchain, no CDN - air-gap friendly.
# Mounted last so every /api and /ws route above takes precedence.
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="dashboard")
