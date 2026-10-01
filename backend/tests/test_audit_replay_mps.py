import json

from sovereign.audit import AuditLog
from sovereign.baselines import run_highs
from sovereign.engine import solve
from sovereign.lp import solve_lp
from sovereign.models.refinery import build
from sovereign.mps import netlib_reference_values, parse_mps
from sovereign.replay import ReplayStore
from sovereign.store import Store

MPS = """NAME          TESTLP
OBJSENSE
    MAX
ROWS
 N  COST
 L  LIM1
 G  LIM2
 E  MYEQN
 L  RNG
COLUMNS
    X1        COST         1.0   LIM1         1.0
    X1        LIM2         1.0
    X2        COST         2.0   LIM1         1.0
    X2        MYEQN       -1.0
    X3        COST        -1.0   MYEQN        1.0
    X3        RNG          1.0
    MARKER                 'MARKER'                 'INTORG'
    Y1        COST         0.5   LIM1         1.0
    MARKER                 'MARKER'                 'INTEND'
RHS
    RHS       LIM1         4.0   LIM2         1.0
    RHS       MYEQN        7.0   RNG          8.0
RANGES
    RNG       RNG          3.0
BOUNDS
 UP BND       X1           4.0
 MI BND       X2
 UP BND       X2           1.0
 UP BND       Y1           3.0
ENDATA
"""


def test_mps_objsense_ranges_bounds_markers():
    m = parse_mps(MPS)
    assert m.maximize and m.name == "TESTLP"
    assert m.var_names == ["X1", "X2", "X3", "Y1"]
    assert m.integer.tolist() == [False, False, False, True]
    assert m.lb[1] == -float("inf") and m.ub[1] == 1.0 and m.ub[0] == 4.0
    assert m.rhs[m.row_names.index("RNG_lo")] == 5.0 and m.rhs[m.row_names.index("RNG_hi")] == 8.0
    s = solve(m)
    h = run_highs(m)
    assert s["status"] == "optimal" and abs(s["objective"] - h["objective"]) < 1e-6
    # MAX problem: the displayed objective is in the user's sense (sign trap, Sec. 12.3)
    assert s["objective"] == -s["objective_internal"]


def test_netlib_readme_parser():
    txt = ("Name       Rows   Cols   Nonzeros    Bytes  BR      Optimal Value\n"
           "AFIRO        28      32       88        794       -4.6475314286E+02\n"
           "FIT2P      3001   13525    60784     439794       6.8464293294E+04\n")
    ref = netlib_reference_values(txt)
    assert ref["AFIRO"] == -464.75314286 and ref["FIT2P"] == 68464.293294


def test_audit_chain_detects_tampering(tmp_path):
    log = AuditLog(Store(tmp_path / "a.db"))
    for i in range(5):
        log.append("evt", "tester", {"i": i})
    assert log.verify()["ok"]
    log.store.conn.execute("UPDATE audit SET payload=? WHERE seq=3", (json.dumps({"i": 99}),))  # edit history
    v = log.verify()
    assert not v["ok"] and v["bad_seq"] == 3 and "hash" in v["reason"]


def test_audit_detects_single_byte_corruption_and_deletion(tmp_path):
    log = AuditLog(Store(tmp_path / "a.db"))
    for i in range(4):
        log.append("evt", "tester", {"value": f"abc{i}"})
    raw = log.store.read("SELECT payload FROM audit WHERE seq=2")[0][0]
    flipped = raw.replace("abc1", "abd1")  # one byte
    log.store.conn.execute("UPDATE audit SET payload=? WHERE seq=2", (flipped,))
    assert log.verify()["bad_seq"] == 2
    log.store.conn.execute("UPDATE audit SET payload=? WHERE seq=2", (raw,))
    assert log.verify()["ok"]
    log.store.conn.execute("DELETE FROM audit WHERE seq=3")  # silently drop an entry
    v = log.verify()
    assert not v["ok"] and "gap" in v["reason"]


def test_external_anchor_detects_full_chain_rewrite(tmp_path):
    log = AuditLog(Store(tmp_path / "a.db"), anchor_dir=tmp_path / "usb")
    for i in range(3):
        log.append("evt", "tester", {"i": i})
    log.anchor()
    assert log.verify()["ok"]
    # an attacker with DB access rewrites the whole chain consistently (and the local anchor table)
    log.store.conn.execute("DELETE FROM audit")
    log.store.conn.execute("DELETE FROM anchors")
    for i in range(5):
        log.append("evt", "tester", {"i": i * 10})
    v = log.verify()  # the anchor on the external medium no longer matches
    assert not v["ok"] and "anchor" in v["reason"]


def test_replay_is_bit_exact(tmp_path):
    rs = ReplayStore(Store(tmp_path / "r.db"))
    m = build()
    b = rs.record(m, solve(m), "t")
    out = rs.replay(b["id"])
    assert out["ok"], out
    assert rs.get(b["id"])["schema_version"] == 2


def test_replay_fails_loudly_on_mismatch(tmp_path):
    rs = ReplayStore(Store(tmp_path / "r.db"))
    m = build()
    b = rs.record(m, solve(m), "t")
    body = rs.get(b["id"])
    body["x_sha256"] = "0" * 64  # simulate a non-reproducible solve
    rs.store.conn.execute("UPDATE bundles SET body=? WHERE id=?", (json.dumps(body), b["id"]))
    out = rs.replay(b["id"])
    assert not out["ok"] and "FAILED" in out["reason"]
    # tampered stored model is caught before solving
    rs.store.conn.execute("UPDATE models SET body=replace(body, '\"c\": [', '\"c\": [1.0, ') "
                          "WHERE fingerprint=?", (b["model_sha256"],))
    out = rs.replay(b["id"])
    assert not out["ok"] and "tampered" in out["reason"]


def test_replay_reads_v1_bundles(tmp_path):
    rs = ReplayStore(Store(tmp_path / "r.db"))
    m = build()
    b = rs.record(m, solve(m), "t")
    body = rs.get(b["id"])
    body.pop("schema_version")
    body.pop("algorithm")
    rs.store.conn.execute("UPDATE bundles SET body=? WHERE id=?", (json.dumps(body), b["id"]))
    up = rs.get(b["id"])
    assert up["schema_version"] == 2 and up["algorithm"] == "mehrotra"
    assert rs.replay(b["id"])["ok"]


def test_repeated_solves_bit_identical():
    m = build().relaxed()
    assert solve_lp(m).x.tobytes() == solve_lp(m).x.tobytes()
