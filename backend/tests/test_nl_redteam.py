"""NL compiler red-team + calibration gate (audit #95, #97, #101). Fails the build if any
adversarial or wrong parse would be applied without a human, or if accuracy regresses."""
from sovereign.nlc.evaluate import evaluate, load_eval


def test_redteam_and_accuracy_offline_rules():
    res = evaluate("sovereign-rules")
    assert res["violations"] == [], res["violations"]
    assert res["parse_accuracy"] == 1.0
    assert res["auto_accept_precision"] in (None, 1.0)
    # anything tagged as red-team never reaches a plan without a human
    for r in res["rows"]:
        if "redteam" in r["tags"]:
            assert r["status"] != "auto_accepted", r["text"]
    # calibration: every populated confidence bucket at least as accurate as its lower bound
    for c in res["calibration"]:
        if c["n"]:
            assert c["accuracy"] >= float(c["bucket"].split("-")[0])


def test_eval_set_covers_the_audit_categories():
    tags = {t for it in load_eval() for t in it.get("tags", [])}
    assert {"injection", "compound", "relative", "decimal-slip", "unit-slip", "homoglyph", "negation",
            "direction", "time", "ambiguous", "unknown-entity"} <= tags
