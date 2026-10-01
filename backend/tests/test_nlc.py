import pytest

from sovereign.audit import AuditLog
from sovereign.store import Store
from sovereign.models.refinery import default_params
from sovereign.nlc import rules
from sovereign.nlc.gate import ConstraintGate, PermissionDenied
from sovereign.nlc.registry import registry

REG = registry(default_params())


@pytest.mark.parametrize("text,kind,entity,sense,value,unit", [
    ("Tank 3 sulfur limit <= 0.2%", "product_spec", "VLSFO", "<=", 0.2, "wt%"),
    ("Tank 3 sulfur limit ≤ 0.2%", "product_spec", "VLSFO", "<=", 0.2, "wt%"),
    ("VLSFO sulphur must not exceed 2000 ppm", "product_spec", "VLSFO", "<=", 2000, "ppm"),
    ("diesel lifting at least 100 kbbl/d", "product_demand", "HSD", ">=", 100, "kbbl/d"),
    ("no more than 1 cargo of Basrah", "crude_limit", "Basrah Medium", "<=", 1, "parcels"),
    ("Reduce FCC capacity to 60 kbbl/d", "unit_capacity", "FCC", "<=", 60, "kbbl/d"),
    ("Tank 1 RON >= 92", "product_spec", "MS", ">=", 92, "RON"),
    ("Bonny Light imports up to 40000 bpd", "crude_limit", "Bonny Light", "<=", 40, "kbbl/d"),
])
def test_rule_parser(text, kind, entity, sense, value, unit):
    p = rules.parse(text, REG)
    assert p.ir is not None, p.error
    assert (p.ir.kind, p.ir.entity, p.ir.sense, p.ir.value, p.ir.unit) == (kind, entity, sense, value, unit)


def test_unit_status_and_ambiguity():
    p = rules.parse("FCC shutdown for maintenance", REG)
    assert p.ir.kind == "unit_status" and p.ir.status == "off"
    p = rules.parse("DHDS capacity max 70", REG)
    assert any("ambiguous" in r for _, r in p.penalties)


def test_compound_sentence_rejected():
    p = rules.parse("Tank 3 fuel oil demand >= 50 kbbl/d, sulfur 0.4%", REG)
    assert p.ir is None and "more than one constraint" in p.error


@pytest.fixture
def gate(tmp_path):
    return ConstraintGate(AuditLog(Store(tmp_path / "g.db")))


def test_decimal_slip_scores_low_and_needs_signoff(gate):
    p = gate.propose("Tank 3 sulfur limit 2.0", "op")
    assert p["status"] == "pending_signoff"
    assert p["confidence"] < 0.6
    assert any(c["check"] == "magnitude" and c["status"] == "warn" for c in p["checks"])


def test_safety_tag_needs_named_signoff_then_shadow_then_promotion(gate):
    p = gate.propose("Tank 3 sulfur limit <= 0.3%", "op")
    assert p["confidence"] == 1.0 and p["safety_tagged"] and p["status"] == "pending_signoff"
    with pytest.raises(ValueError):
        gate.decide(p["id"], "approve", "   ")  # anonymous approval refused
    with pytest.raises(PermissionDenied):  # safety-tagged: an operator may not approve (RBAC)
        gate.decide(p["id"], "approve", "o.kumar", "", role="operator")
    d = gate.decide(p["id"], "approve", "a.sharma", "PSE confirmed", role="supervisor")
    assert d["status"] == "approved" and d["stage"] == "shadow"
    prod = next(s for s in gate.params("production").specs if s.product == "VLSFO")
    shadow = next(s for s in gate.params("shadow").specs if s.product == "VLSFO")
    assert prod.value == 0.5 and shadow.value == 0.3
    with pytest.raises(PermissionDenied):
        gate.promote(gate.active[0].id, "o.kumar", role="operator")
    gate.promote(gate.active[0].id, "a.sharma", role="supervisor")
    assert next(s for s in gate.params("production").specs if s.product == "VLSFO").value == 0.3


def test_confident_non_safety_change_auto_accepts_into_shadow(gate):
    p = gate.propose("diesel lifting at least 100 kbbl/d", "op")
    assert p["status"] == "auto_accepted" and p["stage"] == "shadow"


def test_validator_blocks_unsupported_property(gate):
    p = gate.propose("Tank 5 sulfur <= 0.1%", "op")  # LPG has no modelled sulfur quality
    assert p["status"] == "blocked"
    with pytest.raises(ValueError):
        gate.decide(p["id"], "approve", "op")


def test_infeasibility_probe_attaches_explanation(gate):
    p = gate.propose("HSD sulfur <= 7 ppm", "op")
    probe = next(c for c in p["checks"] if c["check"] == "feasibility probe")
    assert probe["status"] == "warn" and "Relax" in probe["detail"]


def test_llm_mode_falls_back_offline_when_unavailable(gate, monkeypatch):
    monkeypatch.setenv("SOVEREIGN_LOCAL_LLM_URL", "http://127.0.0.1:9/api/chat")  # nothing listens
    p = gate.propose("diesel lifting at least 100 kbbl/d", "op", mode="sovereign-local-llm")
    assert any("fell back" in n for n in p["notes"])
    assert p["ir"]["entity"] == "HSD"


def test_local_llm_refuses_remote_host(monkeypatch):
    from sovereign.nlc.llm import LLMUnavailable, parse_local
    monkeypatch.setenv("SOVEREIGN_LOCAL_LLM_URL", "http://example.com/api/chat")
    with pytest.raises(LLMUnavailable, match="non-loopback"):
        parse_local("x", "")


def test_audit_records_decisions(gate):
    p = gate.propose("Tank 3 sulfur limit <= 0.3%", "op")
    gate.decide(p["id"], "reject", "op", "not today")
    assert [e["event"] for e in gate.audit.entries()] == ["nl.proposed", "nl.rejected"]
    assert gate.audit.verify()["ok"]


def test_storage_failure_is_fail_closed(gate, monkeypatch):
    """Disk full / locked DB during a sign-off: neither the state change nor the audit entry may persist."""
    import sqlite3
    p = gate.propose("Tank 3 sulfur limit <= 0.3%", "op")
    n_audit = len(gate.audit.entries())
    real = gate.audit.append

    def boom(event, *a, **k):
        if event == "nl.approved":
            raise sqlite3.OperationalError("database or disk is full")
        return real(event, *a, **k)

    monkeypatch.setattr(gate.audit, "append", boom)
    with pytest.raises(sqlite3.OperationalError):
        gate.decide(p["id"], "approve", "sup", "", role="supervisor")
    assert gate.active == [] and gate.proposals[p["id"]]["status"] == "pending_signoff"   # memory rolled back
    fresh = ConstraintGate(gate.audit)                                                    # and nothing on disk
    assert fresh.active == [] and fresh.proposals[p["id"]]["status"] == "pending_signoff"
    assert len(gate.audit.entries()) == n_audit and gate.audit.verify()["ok"]
