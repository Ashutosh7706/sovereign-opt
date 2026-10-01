"""Human Verification Gate (Sec. 6) for NL-generated constraints.

  Step 1  parser proposes typed IR + plain-English restatement
  Step 2  symbolic validator: schema, units, physical range, contradiction, redundancy,
          achievability, and a feasibility/impact probe with the real solver
  Step 3  confidence score; below threshold OR safety-tagged (sulfur, pressure, temperature)
          OR any validator warning  ->  mandatory named human sign-off
  Step 4  every transition written to the hash-chained audit log
  Step 5  shadow mode: accepted constraints first run in a parallel advisory plan and only
          reach the production plan after an explicit, audited promotion
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass

from ..audit import AuditLog
from ..engine import SolverConfig, solve
from ..infeasibility import explain_infeasibility
from ..models.refinery import RefineryParams, Spec, build, default_params, _slug
from . import rules
from .llm import LLMUnavailable, parse_cloud, parse_local
from .registry import SAFETY_PROPS, SPEC_PROPS, describe_for_llm, registry
from .schema import ConstraintIR

MODES = ("sovereign-rules", "sovereign-local-llm", "cloud-llm")
THRESHOLDS = {"sovereign-rules": 0.90, "sovereign-local-llm": 0.90, "cloud-llm": 0.80}


# ----------------------------------------------------------------- IR -> parameter delta
def _to_unit(value: float, frm: str, to: str) -> float:
    if frm == to:
        return value
    if frm == "wt%" and to == "ppm":
        return value * 1e4
    if frm == "ppm" and to == "wt%":
        return value / 1e4
    raise ValueError(f"cannot convert {frm} to {to}")


def apply_ir(p: RefineryParams, ir: ConstraintIR) -> tuple[RefineryParams, dict]:
    """Return a NEW params object with the change, plus {'field','old','new','unit'}."""
    q = p.copy()
    e = ir.entity
    if ir.kind == "product_spec":
        spec = next((s for s in q.specs if s.product == e and s.prop == ir.prop and s.sense == ir.sense), None)
        if spec is None:
            unit = "RON" if ir.prop == "ron" else ir.unit
            spec = Spec(e, ir.prop, ir.sense, ir.value, unit, safety=ir.prop in SAFETY_PROPS)
            q.specs.append(spec)
            return q, {"field": f"{e} {ir.prop} {ir.sense}", "old": None, "new": ir.value, "unit": unit}
        new = ir.value if ir.prop == "ron" else _to_unit(ir.value, ir.unit, spec.unit)
        old = spec.value
        spec.value = new
        return q, {"field": f"{e} {ir.prop} {ir.sense}", "old": old, "new": new, "unit": spec.unit}
    if ir.kind == "product_demand":
        pr = q.products[e]
        if ir.sense == ">=":
            old, pr.min_demand = pr.min_demand, ir.value
            return q, {"field": f"{e} minimum lifting", "old": old, "new": ir.value, "unit": "kbbl/d"}
        if ir.sense == "<=":
            old, pr.max_demand = pr.max_demand, ir.value
            return q, {"field": f"{e} market limit", "old": old, "new": ir.value, "unit": "kbbl/d"}
        old = (pr.min_demand, pr.max_demand)
        pr.min_demand = pr.max_demand = ir.value
        return q, {"field": f"{e} fixed lifting", "old": old[0], "new": ir.value, "unit": "kbbl/d"}
    if ir.kind == "unit_capacity":
        if e == "CDU":
            if ir.sense == ">=":
                old, q.cdu_min = q.cdu_min, ir.value
                return q, {"field": "CDU minimum run", "old": old, "new": ir.value, "unit": "kbbl/d"}
            old, q.cdu_capacity = q.cdu_capacity, ir.value
            if ir.sense == "==":
                q.cdu_min = ir.value
            return q, {"field": "CDU capacity", "old": old, "new": ir.value, "unit": "kbbl/d"}
        u = q.units[e]
        if ir.sense == ">=":
            old, u.min_run = u.min_run, ir.value
            return q, {"field": f"{e} minimum run", "old": old, "new": ir.value, "unit": "kbbl/d"}
        old, u.capacity = u.capacity, ir.value
        if ir.sense == "==":
            u.min_run = ir.value
            u.status = "on"
        return q, {"field": f"{e} capacity", "old": old, "new": ir.value, "unit": "kbbl/d"}
    if ir.kind == "unit_status":
        u = q.units[e]
        old, u.status = u.status, ir.status
        return q, {"field": f"{e} status", "old": old, "new": ir.status, "unit": ""}
    if ir.kind == "crude_limit":
        c = next(c for c in q.crudes if c.name == e)
        ex = q.extra_crude_bounds.setdefault(e, {})
        if ir.unit == "parcels":
            keys = {"<=": ["max_parcels"], ">=": ["min_parcels"], "==": ["max_parcels", "min_parcels"]}[ir.sense]
            old = ex.get(keys[0], c.max_parcels if keys[0] == "max_parcels" else 0)
            for k in keys:
                ex[k] = int(round(ir.value))
            return q, {"field": f"{e} parcels {ir.sense}", "old": old, "new": int(round(ir.value)), "unit": "parcels"}
        keys = {"<=": ["max_run"], ">=": ["min_run"], "==": ["max_run", "min_run"]}[ir.sense]
        old = ex.get(keys[0])
        for k in keys:
            ex[k] = ir.value
        return q, {"field": f"{e} crude run {ir.sense}", "old": old, "new": ir.value, "unit": "kbbl/d"}
    raise ValueError(ir.kind)


def restate(ir: ConstraintIR, p: RefineryParams, change: dict | None) -> str:
    e = ir.entity
    tank = f"Tank {p.products[e].tank} " if e in p.products else ""
    word = {"<=": "at most", ">=": "at least", "==": "exactly"}.get(ir.sense or "", "")
    cur = ""
    if change and change.get("old") is not None and change["old"] != change["new"]:
        cur = f" (currently {change['old']:g} {change['unit']})" if isinstance(change["old"], (int, float)) \
            else f" (currently {change['old']})"
    if ir.kind == "product_spec":
        label = "sulfur content" if ir.prop == "sulfur" else "research octane number (RON)"
        conv = ""
        if change and ir.unit and change["unit"] != ir.unit and ir.prop == "sulfur":
            conv = f" = {change['new']:g} {change['unit']}"
        return f"{tank}{e} {label} must be {word} {ir.value:g} {ir.unit}{conv}{cur}."
    if ir.kind == "product_demand":
        what = {">=": "contract lifting of", "<=": "sales capped at", "==": "lifting fixed at"}[ir.sense]
        return f"{tank}{e}: {what} {ir.value:g} kbbl/d{cur}."
    if ir.kind == "unit_capacity":
        what = {"<=": "feed limited to at most", ">=": "minimum run (when operating) of at least",
                "==": "feed fixed at"}[ir.sense]
        return f"{e} {what} {ir.value:g} kbbl/d{cur}."
    if ir.kind == "unit_status":
        return f"{e} is forced {'OFF (shut down / not available)' if ir.status == 'off' else 'ON (must run)'}{cur}."
    unit = "parcels" if ir.unit == "parcels" else "kbbl/d of crude run"
    return f"Crude {e}: {word} {ir.value:g} {unit}{cur}."


def render_math(ir: ConstraintIR, p: RefineryParams) -> list[str]:
    """Show the exact solver rows / bounds the change produces."""
    m = build(p)
    e = _slug(ir.entity)
    rows, bounds = [], []
    if ir.kind == "product_spec":
        rows = [f"spec[{e},{ir.prop},{'max' if ir.sense == '<=' else 'min'}]"]
    elif ir.kind == "product_demand":
        rows = {"<=": [f"demand_max[{e}]"], ">=": [f"demand_min[{e}]"],
                "==": [f"demand_min[{e}]", f"demand_max[{e}]"]}[ir.sense]
    elif ir.kind == "unit_capacity":
        if ir.entity == "CDU":
            rows = {"<=": ["cdu_capacity"], ">=": ["cdu_min"], "==": ["cdu_capacity", "cdu_min"]}[ir.sense]
        else:
            rows = {"<=": [f"cap[{e}]"], ">=": [f"minrun[{e}]"], "==": [f"cap[{e}]", f"minrun[{e}]"]}[ir.sense]
    elif ir.kind == "unit_status":
        bounds = [f"on[{e}]"]
    else:
        bounds = [f"parcels[{e}]", f"crude[{e}]"]
    out = []
    sym = {"L": "<=", "G": ">=", "E": "="}
    for rn in rows:
        if rn not in m.row_names:
            continue
        i = m.row_names.index(rn)
        r = m.A.getrow(i).tocoo()
        terms = sorted(zip(r.col, r.data), key=lambda t: -abs(t[1]))
        parts = [f"{'-' if a < 0 else '+'} {abs(a):.4g}·{m.var_names[j]}" for j, a in terms[:8]]
        more = f" + … ({len(terms) - 8} more terms)" if len(terms) > 8 else ""
        expr = " ".join(parts).lstrip("+ ").strip()
        out.append(f"{rn}:  {expr}{more}  {sym[m.senses[i]]}  {m.rhs[i]:.6g}")
    for vn in bounds:
        j = m.var_names.index(vn)
        out.append(f"bound:  {m.lb[j]:g} <= {vn} <= {m.ub[j]:g}" + ("  (integer)" if m.integer[j] else ""))
    return out


# ----------------------------------------------------------------- validator
def validate(ir: ConstraintIR, p: RefineryParams) -> tuple[list[dict], float]:
    checks: list[dict] = []
    penalty = 0.0

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    reg = registry(p)
    # schema / binding
    known = (ir.entity in p.products if ir.kind in ("product_spec", "product_demand") else
             ir.entity in p.units or ir.entity == "CDU" if ir.kind in ("unit_capacity", "unit_status") else
             ir.entity in [c.name for c in p.crudes])
    if not known:
        add("entity binding", "fail", f"'{ir.entity}' is not a {ir.kind.split('_')[0]} in the registry")
        return checks, 1.0
    add("entity binding", "pass", f"bound to registry entity '{ir.entity}'")
    if ir.kind == "product_spec":
        allowed = SPEC_PROPS.get(ir.entity, set())
        if ir.prop not in allowed:
            add("property", "fail", f"{ir.entity} has no modelled '{ir.prop}' quality (supported: {sorted(allowed) or 'none'})")
            return checks, 1.0
        if ir.prop == "sulfur" and ir.unit not in ("wt%", "ppm"):
            add("units", "fail", f"sulfur needs wt% or ppm, got '{ir.unit}'")
            return checks, 1.0
        add("units", "pass", f"{ir.unit} is a valid {ir.prop} unit")
    if ir.kind == "unit_status" and ir.entity == "CDU":
        add("physical range", "fail", "the crude unit cannot be shut down in a running-plant plan")
        return checks, 1.0
    if ir.value is not None and ir.value < 0:
        add("physical range", "fail", "negative value")
        return checks, 1.0
    # physical range
    if ir.kind == "product_spec" and ir.prop == "sulfur" and ir.sense in (">=", "=="):
        add("direction", "warn", "a MINIMUM (or exact) sulfur content is unusual for a fuel spec - "
                                 "did you mean a maximum?")
        penalty += 0.30
    if ir.kind == "product_spec" and ir.prop == "sulfur":
        wt = _to_unit(ir.value, ir.unit, "wt%")
        ok = 0 <= wt <= 5
        add("physical range", "pass" if ok else "fail", f"{wt:g} wt% sulfur ({'plausible' if ok else 'outside 0-5 wt%'})")
        if not ok:
            return checks, 1.0
    if ir.kind == "product_spec" and ir.prop == "ron":
        ok = 60 <= ir.value <= 110
        add("physical range", "pass" if ok else "fail", f"RON {ir.value:g} ({'plausible' if ok else 'outside 60-110'})")
        if not ok:
            return checks, 1.0
    if ir.kind in ("product_demand", "unit_capacity") or (ir.kind == "crude_limit" and ir.unit == "kbbl/d"):
        ok = ir.value <= 1000
        add("physical range", "pass" if ok else "fail", f"{ir.value:g} kbbl/d")
        if not ok:
            return checks, 1.0
    if ir.kind == "crude_limit" and ir.unit == "parcels":
        ok = float(ir.value).is_integer() and ir.value <= 10
        add("physical range", "pass" if ok else "fail",
            f"{ir.value:g} parcels ({'integer' if float(ir.value).is_integer() else 'parcels must be whole'})")
        if not ok:
            return checks, 1.0

    new_p, change = apply_ir(p, ir)
    # magnitude check vs current value (catches 0.2 vs 2.0, % vs ppm slips)
    old, new = change.get("old"), change.get("new")
    if ir.kind != "crude_limit" and isinstance(old, (int, float)) and isinstance(new, (int, float)) and old and new:
        ratio = new / old
        if ratio >= 3 or ratio <= 1 / 3:
            add("magnitude", "warn", f"new value is {ratio:.3g}x the current {old:g} {change['unit']} - "
                                     f"possible decimal-point or unit slip")
            penalty += 0.30
        else:
            add("magnitude", "pass", f"{ratio:.3g}x the current value")
    # contradictions
    for pn, pr in new_p.products.items():
        if pr.min_demand > pr.max_demand:
            add("contradiction", "fail", f"{pn} minimum lifting {pr.min_demand:g} exceeds market limit {pr.max_demand:g}")
            return checks, 1.0
    for un, u in new_p.units.items():
        if u.min_run > u.capacity:
            add("contradiction", "fail", f"{un} minimum run {u.min_run:g} exceeds capacity {u.capacity:g}")
            return checks, 1.0
    if new_p.cdu_min > new_p.cdu_capacity:
        add("contradiction", "fail", f"CDU minimum {new_p.cdu_min:g} exceeds capacity {new_p.cdu_capacity:g}")
        return checks, 1.0
    for cn, ex in new_p.extra_crude_bounds.items():
        if ex.get("min_parcels", 0) > ex.get("max_parcels", 99):
            add("contradiction", "fail", f"{cn} minimum parcels exceed maximum")
            return checks, 1.0
    add("contradiction", "pass", "no direct conflict with other active limits")
    # redundancy / achievability for quality specs (interval reasoning over components)
    if ir.kind == "product_spec":
        m = build(new_p)
        rn = f"spec[{_slug(ir.entity)},{ir.prop},{'max' if ir.sense == '<=' else 'min'}]"
        row = m.A.getrow(m.row_names.index(rn)).tocoo()
        coefs = row.data  # (q_k - spec) * scale
        if ir.sense == "<=":
            if (coefs <= 1e-12).all():
                add("redundancy", "warn", "every blend component already meets this limit - constraint has no effect")
                penalty += 0.10
            elif (coefs > 0).all():
                add("achievability", "warn", f"no component meets it: {ir.entity} can only be made at zero volume")
                penalty += 0.10
            else:
                add("achievability", "pass", "some components meet it - blending can satisfy it")
        else:
            if (coefs >= -1e-12).all():
                add("redundancy", "warn", "every component already satisfies this minimum - no effect")
                penalty += 0.10
            elif (coefs < 0).all():
                add("achievability", "warn", f"no component reaches it: {ir.entity} can only be made at zero volume")
                penalty += 0.10
            else:
                add("achievability", "pass", "achievable by blending")
    return checks, penalty


# ----------------------------------------------------------------- gate / state
ROLE_RANK = {"operator": 1, "supervisor": 2, "admin": 3}


class PermissionDenied(PermissionError):
    pass


def require_role(role: str, needed: str, what: str) -> None:
    if ROLE_RANK.get(role, 0) < ROLE_RANK[needed]:
        raise PermissionDenied(f"{what} requires the {needed} role (you are {role or 'unauthenticated'})")


@dataclass
class ActiveConstraint:
    id: str
    proposal_id: str
    text: str
    ir: dict
    stage: str  # shadow | production
    approved_by: str
    approved_at: str
    restatement: str
    parser: str = "offline rule parser"  # which parser/model produced the IR (audit #100)


def parser_id(mode: str) -> str:
    """Version-pinned identity of the parser that produced an IR."""
    if mode == "sovereign-local-llm":
        return "local:" + os.environ.get("SOVEREIGN_LOCAL_LLM_MODEL", "llama3.1:8b")
    if mode == "cloud-llm":
        return "cloud:" + os.environ.get("SOVEREIGN_CLOUD_MODEL", "claude-opus-5-5")
    return "rules:" + rules.RULES_VERSION


class ConstraintGate:
    """All mutations run inside ONE SQLite transaction together with their audit entries."""

    def __init__(self, audit: AuditLog, solver_cfg: SolverConfig | None = None,
                 cloud_allowed: bool = False, lan_llm_allowed: bool = False):
        self.audit = audit
        self.store = audit.store
        self.solver_cfg = solver_cfg or SolverConfig()
        self.cloud_allowed = cloud_allowed
        self.lan_llm_allowed = lan_llm_allowed
        self._lock = threading.RLock()
        self._impact_cache: dict[str, dict] = {}
        self.llm_failures = 0
        self._load()

    # persistence
    def _load(self):
        self.settings = {"nl_mode": "sovereign-rules", "shadow_mode": True}
        self.settings.update(self.store.get("settings", {}))
        if self.settings["nl_mode"] == "cloud-llm" and not self.cloud_allowed:
            self.settings["nl_mode"] = "sovereign-rules"
        self.proposals = {r["id"]: json.loads(r["body"]) for r in
                          self.store.read("SELECT id, body FROM proposals ORDER BY created, rowid")}
        self.active = [ActiveConstraint(**json.loads(r["body"])) for r in
                       self.store.read("SELECT body FROM constraints ORDER BY position")]

    def _write(self, c, prop: dict | None = None):
        c.execute("INSERT INTO kv(key, value) VALUES('settings', ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(self.settings),))
        if prop is not None:
            c.execute("INSERT INTO proposals(id, created, body) VALUES(?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                      (prop["id"], prop["created"], json.dumps(prop, default=str)))
        c.execute("DELETE FROM constraints")
        for i, a in enumerate(self.active):
            c.execute("INSERT INTO constraints(id, position, body) VALUES(?,?,?)", (a.id, i, json.dumps(asdict(a))))

    @contextlib.contextmanager
    def _mutation(self):
        """Fail closed: if the database write (state or audit) fails, nothing is applied."""
        with self._lock:
            try:
                with self.store.tx() as c:
                    yield c
            except BaseException:
                self._load()
                raise

    # parameters
    def params(self, stage: str = "production") -> RefineryParams:
        p = default_params()
        for a in self.active:
            if a.stage == "production" or stage == "shadow":
                p, _ = apply_ir(p, ConstraintIR(**a.ir))
        return p

    def set_settings(self, actor: str, role: str = "admin", **kw):
        require_role(role, "admin", "changing platform settings")
        with self._mutation() as c:
            old = dict(self.settings)
            for k, v in kw.items():
                if v is None:
                    continue
                if k == "nl_mode":
                    if v not in MODES:
                        raise ValueError(f"unknown mode {v}")
                    if v == "cloud-llm" and not self.cloud_allowed:
                        raise PermissionDenied("cloud-llm is disabled in this build (SKU-2 sovereign lock)")
                self.settings[k] = v
            self.audit.append("settings.changed", actor, {"old": old, "new": self.settings})
            self._write(c)
            return self.settings

    def _impact(self, p: RefineryParams) -> dict:
        m = build(p)
        fp = m.fingerprint()
        if fp not in self._impact_cache:
            r = solve(m, self.solver_cfg)
            self._impact_cache[fp] = {"status": r["status"], "objective": r["objective"]}
            if len(self._impact_cache) > 128:
                self._impact_cache.pop(next(iter(self._impact_cache)))
        return self._impact_cache[fp]

    # step 1-3
    def propose(self, text: str, operator: str, mode: str | None = None, role: str = "operator") -> dict:
        require_role(role, "operator", "proposing a constraint")
        mode = mode or self.settings["nl_mode"]
        if mode == "cloud-llm" and not self.cloud_allowed:
            raise PermissionDenied("cloud-llm is disabled in this build (SKU-2 sovereign lock)")
        base = self.params("shadow")
        reg = registry(base)
        rp = rules.parse(text, reg)
        notes = list(rp.notes)
        penalties = list(rp.penalties)
        source = "offline rule parser"
        used_parser = parser_id("sovereign-rules")
        ir = rp.ir
        llm_info = None
        llm_event = None
        if mode in ("sovereign-local-llm", "cloud-llm"):
            try:
                fn = parse_local if mode == "sovereign-local-llm" else parse_cloud
                lp = fn(text, describe_for_llm(base))
                used_parser = parser_id(mode)
                llm_info = {"restatement": lp.restatement, "confidence": lp.confidence, "ambiguities": lp.ambiguities}
                source = "on-prem open-weight LLM" if mode == "sovereign-local-llm" else "cloud LLM (Claude)"
                penalties = [(0.10, f"LLM ambiguity: {a}") for a in lp.ambiguities]
                penalties.append((max(0.0, 1.0 - lp.confidence), "LLM self-reported uncertainty"))
                if lp.ir is None:
                    ir = None
                    rp.error = rp.error or "LLM found no single supported constraint"
                else:
                    if rp.ir is None:
                        penalties.append((0.10, "offline rule parser could not independently confirm the parse"))
                    elif not _same(rp.ir, lp.ir):
                        penalties.append((0.40, "offline parser disagrees: it read "
                                          + str(rp.ir.model_dump(exclude_none=True))))
                    ir = lp.ir
                self.llm_failures = 0
            except LLMUnavailable as e:
                # degrade to the offline parser for this request and alert the operator (audit #98)
                self.llm_failures += 1
                notes.append(f"ALERT: {mode} unavailable ({e}); fell back to the offline rule parser")
                penalties.append((0.05, "LLM unavailable - offline parser used"))
                llm_event = {"mode": mode, "error": str(e)[:300], "consecutive_failures": self.llm_failures}

        pid = uuid.uuid4().hex[:10]
        relax = None
        if mode == "sovereign-local-llm" and self.lan_llm_allowed:
            relax = "SOVEREIGN_ALLOW_LAN_LLM active: non-loopback on-prem LLM host permitted"
        prop = {"id": pid, "text": text, "operator": operator, "mode": mode, "source": source,
                "parser": used_parser, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "notes": notes,
                "llm": llm_info, "evidence": _jsonify(rp.evidence), "sovereignty_relaxation": relax}
        if ir is None:
            prop.update({"status": "unparsed", "error": rp.error or "not understood", "confidence": 0.0,
                         "requires_signoff": False, "checks": [], "ir": None,
                         "restatement": "Could not map this sentence to a supported constraint: " + (rp.error or "")})
            with self._mutation() as c:
                if llm_event:
                    self.audit.append("nl.llm_unavailable", "system", llm_event)
                self.audit.append("nl.unparsed", operator, {"proposal": pid, "text": text, "error": prop["error"],
                                                            "parser": used_parser})
                self.proposals[pid] = prop
                self._write(c, prop)
            return prop

        checks, vpen = validate(ir, base)
        fatal = any(c["status"] == "fail" for c in checks)
        change = None
        math = []
        impact = None
        if not fatal:
            new_p, change = apply_ir(base, ir)
            math = render_math(ir, new_p)
            before = self._impact(base)
            after = self._impact(new_p)
            impact = {"before": before, "after": after}
            if after["status"] not in ("optimal", "feasible"):
                exp = explain_infeasibility(build(new_p), self.solver_cfg.ipm())
                impact["explanation"] = exp["summary"]
                checks.append({"check": "feasibility probe", "status": "warn",
                               "detail": "plan becomes INFEASIBLE with this constraint. " + exp["summary"]})
                vpen += 0.10
            else:
                d = (after["objective"] or 0) - (before["objective"] or 0)
                checks.append({"check": "feasibility probe", "status": "pass",
                               "detail": f"plan stays feasible; margin {d:+,.1f} $k/d"})
        total_pen = sum(a for a, _ in penalties) + vpen
        conf = 0.0 if fatal else max(0.0, min(1.0, 1.0 - total_pen))
        safety = ir.kind == "product_spec" and ir.prop in SAFETY_PROPS
        threshold = THRESHOLDS[mode]
        warns = [c for c in checks if c["status"] == "warn"]
        reasons = []
        if safety:
            reasons.append(f"touches safety-tagged variable '{ir.prop}' (supervisor sign-off)")
        if conf < threshold:
            reasons.append(f"confidence {conf:.2f} < {threshold:.2f} threshold ({mode})")
        if warns:
            reasons.append(f"{len(warns)} validator warning(s)")
        prop.update({
            "ir": ir.model_dump(), "restatement": restate(ir, base, change),
            "llm_restatement": llm_info["restatement"] if llm_info else None,
            "math": math, "change": change, "checks": checks, "impact": impact,
            "confidence": round(conf, 3), "threshold": threshold,
            "penalties": [{"amount": round(a, 3), "reason": r} for a, r in penalties],
            "safety_tagged": safety, "requires_signoff": bool(reasons) and not fatal,
            "signoff_reasons": reasons, "required_role": "supervisor" if safety else "operator",
            "status": "blocked" if fatal else ("pending_signoff" if reasons else "auto_accepted"),
        })
        with self._mutation() as c:
            if llm_event:
                self.audit.append("nl.llm_unavailable", "system", llm_event)
            self.audit.append("nl.proposed", operator, {"proposal": pid, "text": text, "ir": prop["ir"], "math": math,
                                                        "confidence": prop["confidence"], "mode": mode,
                                                        "parser": used_parser, "status": prop["status"],
                                                        "sovereignty_relaxation": relax})
            self.proposals[pid] = prop
            if prop["status"] == "auto_accepted":
                self._activate(prop, f"auto-policy (confidence {conf:.2f} >= {threshold:.2f}, no safety tag)")
            elif fatal:
                self.audit.append("nl.blocked", "validator", {"proposal": pid,
                                                              "failed": [c for c in checks if c["status"] == "fail"]})
            self._write(c, prop)
        return prop

    def _activate(self, prop: dict, approver: str) -> ActiveConstraint:
        stage = "shadow" if self.settings.get("shadow_mode", True) else "production"
        ac = ActiveConstraint(uuid.uuid4().hex[:8], prop["id"], prop["text"], prop["ir"], stage, approver,
                              time.strftime("%Y-%m-%dT%H:%M:%S%z"), prop["restatement"],
                              prop.get("parser", "offline rule parser"))
        key = _knob(ConstraintIR(**prop["ir"]))  # a newer constraint on the same knob supersedes the older one
        for a in list(self.active):
            if _knob(ConstraintIR(**a.ir)) == key and a.stage == stage:
                self.active.remove(a)
                self.audit.append("constraint.superseded", approver, {"constraint": a.id, "by": ac.id})
        self.active.append(ac)
        prop["active_id"] = ac.id
        prop["stage"] = stage
        self.audit.append("nl.accepted", approver, {"proposal": prop["id"], "constraint": ac.id, "stage": stage,
                                                    "text": prop["text"], "ir": prop["ir"], "math": prop.get("math"),
                                                    "parser": ac.parser})
        return ac

    # step 3b - human decision
    def decide(self, pid: str, action: str, operator: str, note: str = "", role: str = "operator") -> dict:
        if not operator or not operator.strip():
            raise ValueError("a named operator is required for sign-off")
        with self._mutation() as c:
            prop = self.proposals.get(pid)
            if prop is None:
                raise KeyError(pid)
            if prop["status"] != "pending_signoff":
                raise ValueError(f"proposal is '{prop['status']}', not pending sign-off")
            if action == "approve":
                require_role(role, prop.get("required_role", "operator"),
                             "approving a safety-tagged constraint" if prop.get("safety_tagged") else "approving")
                prop["status"] = "approved"
                prop["decided_by"] = operator
                prop["decided_role"] = role
                prop["decision_note"] = note
                self._activate(prop, operator)
                self.audit.append("nl.approved", operator, {"proposal": pid, "note": note, "role": role,
                                                            "confidence": prop["confidence"],
                                                            "reasons": prop["signoff_reasons"]})
            elif action == "reject":
                require_role(role, "operator", "rejecting")
                prop["status"] = "rejected"
                prop["decided_by"] = operator
                prop["decision_note"] = note
                self.audit.append("nl.rejected", operator, {"proposal": pid, "note": note, "role": role})
            else:
                raise ValueError(action)
            self._write(c, prop)
            return prop

    def promote(self, cid: str, operator: str, note: str = "", role: str = "operator") -> ActiveConstraint:
        if not operator.strip():
            raise ValueError("a named operator is required")
        require_role(role, "supervisor", "promoting a shadow constraint to production")
        with self._mutation() as c:
            a = next(a for a in self.active if a.id == cid)
            if a.stage == "production":
                raise ValueError("already in production")
            key = _knob(ConstraintIR(**a.ir))
            for b in list(self.active):
                if b is not a and b.stage == "production" and _knob(ConstraintIR(**b.ir)) == key:
                    self.active.remove(b)
                    self.audit.append("constraint.superseded", operator, {"constraint": b.id, "by": a.id})
            a.stage = "production"
            self.audit.append("constraint.promoted", operator, {"constraint": cid, "text": a.text, "note": note,
                                                                "role": role})
            self._write(c)
            return a

    def retire(self, cid: str, operator: str, note: str = "", role: str = "operator") -> None:
        if not operator.strip():
            raise ValueError("a named operator is required")
        require_role(role, "supervisor", "retiring a constraint")
        with self._mutation() as c:
            a = next(a for a in self.active if a.id == cid)
            self.active.remove(a)
            self.audit.append("constraint.retired", operator, {"constraint": cid, "text": a.text, "note": note,
                                                               "role": role})
            self._write(c)

    def needs_reverification(self) -> list[dict]:
        """Active constraints produced by a parser/model other than the one configured now (#100)."""
        current = {parser_id(m) for m in MODES}
        return [asdict(a) for a in self.active if a.parser not in current]


def _knob(ir: ConstraintIR) -> tuple:
    return (ir.kind, ir.entity, ir.prop, ir.sense if ir.kind != "unit_status" else None,
            ir.unit if ir.kind == "crude_limit" else None)


def _canon_value(ir: ConstraintIR):
    if ir.kind == "product_spec" and ir.prop == "sulfur" and ir.value is not None:
        return round(_to_unit(ir.value, ir.unit, "ppm"), 6)
    return ir.value


def _same(a: ConstraintIR, b: ConstraintIR) -> bool:
    return (a.kind, a.entity, a.prop, a.sense, a.status) == (b.kind, b.entity, b.prop, b.sense, b.status) and \
        _canon_value(a) == _canon_value(b)


def _jsonify(d):
    return json.loads(json.dumps(d, default=str))
