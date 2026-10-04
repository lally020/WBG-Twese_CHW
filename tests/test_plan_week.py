"""Step 9: hours change the number of visits; villages grouped; emergencies never planned; edits stored."""

from engine import plan_week, risk, signals
from tests.conftest import END

WEEK = "2026-10-05"


def _ready(con, cfg):
    for (pid,) in con.execute("SELECT id FROM patients").fetchall():
        signals.refresh(con, cfg, pid, END)
    risk.risk_all(con, cfg, END)


def test_hours_change_visits(con, cfg):
    _ready(con, cfg)
    few = plan_week.plan_week(con, cfg, 1, WEEK, 3, regenerate=True)["counts"]["visit"]
    many = plan_week.plan_week(con, cfg, 1, WEEK, 20, regenerate=True)["counts"]["visit"]
    assert many > few > 0


def test_villages_grouped_and_no_emergencies(con, cfg):
    _ready(con, cfg)
    plan = plan_week.plan_week(con, cfg, 1, WEEK, 20, regenerate=True)
    esc = set(plan["escalation_patient_ids"])
    assert esc
    assert not esc & {r["patient_id"] for r in plan["rows"]}
    # Every village appears once in the route, and later visits there share the travel.
    villages = [v["village"] for v in plan["route"]]
    assert len(villages) == len(set(villages))
    assert any("travel shared" in r["reason_text"] for r in plan["rows"] if r["action"] == "visit")
    assert plan["minutes_used"] <= 20 * 60


def test_update_and_approve(con, cfg):
    _ready(con, cfg)
    plan = plan_week.plan_week(con, cfg, 1, WEEK, 20, regenerate=True)
    row = next(r for r in plan["rows"] if r["action"] != "visit")
    after = plan_week.update_plan(con, cfg, row["id"], "visit")
    moved = next(r for r in after["rows"] if r["id"] == row["id"])
    assert moved["action"] == "visit" and "Changed by CHW" in moved["reason_text"]
    assert not after["approved"]
    assert plan_week.approve_plan(con, cfg, 1, WEEK)["approved"]
    # An approved plan is kept, even if asked again with other hours.
    assert plan_week.plan_week(con, cfg, 1, WEEK, 5)["approved"]


def test_plan_only_has_this_chws_patients(con, cfg):
    _ready(con, cfg)
    con.execute("UPDATE patients SET chw_id = 2 WHERE id > 30")
    con.commit()
    plan = plan_week.plan_week(con, cfg, 1, WEEK, 20, regenerate=True)
    assert plan["rows"] and all(r["patient_id"] <= 30 for r in plan["rows"])
    plan2 = plan_week.plan_week(con, cfg, 2, WEEK, 20, regenerate=True)
    assert all(r["patient_id"] > 30 for r in plan2["rows"])


def test_escalation_query_matches_patient_by_patient(con, cfg):
    _ready(con, cfg)
    from engine import decide
    decide.decide(con, cfg, 3, END)  # a stored decision, to exercise the decisions part
    old = set()
    for (pid,) in con.execute("SELECT id FROM patients WHERE status = 'active'").fetchall():
        if any(f["emergency"] == "yes" for f in signals.open_flags(con, pid)):
            old.add(pid)
            continue
        d = con.execute("SELECT suggestion, final_choice FROM decisions WHERE patient_id = ? "
                        "ORDER BY date DESC, id DESC LIMIT 1", (pid,)).fetchone()
        if d and (d["final_choice"] or d["suggestion"]) == "emergency_now":
            old.add(pid)
    assert plan_week.escalation_ids(con) == old and old
