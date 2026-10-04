"""Step 11c: catching false positives and false negatives (PLAN.md)."""

from engine import errors
from engine.common import dumps
from tests.conftest import END


def _patient(con, pid):
    con.execute("INSERT INTO patients (id, sex, age, village, language, condition_codes) "
                "VALUES (?, 'male', 60, 'Ngozi', 'sw', '[\"hypertension\"]')", (pid,))


def test_event_without_prior_flag_is_false_negative_with_cause_silence(con, cfg):
    _patient(con, 970)
    con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes) "
                "VALUES (970, '2026-03-01', 'chw', 132, 82, '132/82', '[]')")  # long before the window
    con.execute("INSERT INTO events (patient_id, date, type, source) VALUES (970, '2026-10-01', 'stroke', 'clinic')")
    errors.detect(con, cfg, END)
    row = con.execute("SELECT * FROM error_reviews WHERE source = 'event_without_flag' AND patient_id = 970").fetchone()
    assert row["kind"] == "false_negative" and row["cause_code"] == "silence"


def test_event_cause_extraction(con, cfg):
    _patient(con, 971)
    con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes, chw_notes_text) "
                "VALUES (971, '2026-09-20', 'chw', 132, 82, '132/82', '[]', 'Patient complains of dizziness today.')")
    con.execute("INSERT INTO events (patient_id, date, type, source) VALUES (971, '2026-10-01', 'stroke', 'clinic')")
    errors.detect(con, cfg, END)
    row = con.execute("SELECT * FROM error_reviews WHERE source = 'event_without_flag' AND patient_id = 971").fetchone()
    assert row["cause_code"] == "extraction" and "dizziness" in row["evidence_json"]


def test_override_down_is_false_positive(con, cfg):
    con.execute("DELETE FROM decisions")
    con.execute("INSERT INTO decisions (patient_id, date, state_json, suggestion, final_choice, model_version, config_version) "
                "VALUES (2, '2026-10-01', ?, 'visit_this_week', 'routine', 'rules-v1', 'v'), "
                "(3, '2026-10-01', '{}', 'routine', 'refer_clinic', 'rules-v1', 'v')",
                (dumps({"flags": [{"type": "trend", "value": "+12"}]}),))
    errors.detect(con, cfg, END)
    rows = {r["patient_id"]: r for r in con.execute("SELECT * FROM error_reviews WHERE source LIKE 'override%'")}
    assert rows[2]["source"] == "override_down" and rows[2]["kind"] == "false_positive" and rows[2]["rule_id"] == "trend"
    assert rows[3]["source"] == "override_up" and rows[3]["kind"] == "false_negative"


def test_corrected_field_is_extraction_error(con, cfg):
    parsed = {"fields": {"sbp": 150, "dbp": 95, "meds_taken": "yes", "symptoms": []},
              "confirmed_fields": {"sbp": 150, "dbp": 85, "meds_taken": "yes", "symptoms": []},
              "llm": {"model": "gemma3:4b"}, "confirmed": True}
    mid = con.execute("INSERT INTO messages (patient_id, direction, kind, lang, text, received_at, parsed_json) "
                      "VALUES (2, 'in', 'report', 'sw', 'presha 150/85', '2026-10-02T08:00:00', ?)",
                      (dumps(parsed),)).lastrowid
    errors.detect(con, cfg, END)
    row = con.execute("SELECT * FROM error_reviews WHERE source = 'field_corrected' AND linked_id = ?", (mid,)).fetchone()
    assert row["kind"] == "extraction_error" and row["rule_id"] == "extraction:dbp" and row["model_version"] == "gemma3:4b"


def test_referral_sent_home_and_both_trajectory_misses(con, cfg):
    did = con.execute("INSERT INTO decisions (patient_id, date, state_json, suggestion, final_choice, "
                      "outcome_at_next_contact) VALUES (4, '2026-09-25', '{}', 'refer_clinic', 'refer_clinic', "
                      "'referral: sent_home (2026-09-28)')").lastrowid
    con.execute("INSERT INTO trajectory_scores (patient_id, date, share_peers_with_event, flag, model_version) "
                "VALUES (5, '2026-09-01', 0.2, 'no', 'knn-v2')")
    eid = con.execute("INSERT INTO events (patient_id, date, type, source) VALUES (5, '2026-09-20', 'hospital_admission', "
                      "'clinic')").lastrowid
    tid = con.execute("INSERT INTO trajectory_scores (patient_id, date, share_peers_with_event, flag, model_version) "
                      "VALUES (6, '2026-04-01', 0.7, 'yes', 'knn-v2')").lastrowid  # horizon passed, no event
    errors.detect(con, cfg, END)
    s = {(r["source"], r["kind"], r["linked_id"]) for r in con.execute("SELECT * FROM error_reviews")}
    assert ("referral_sent_home", "false_positive", did) in s
    assert ("trajectory_miss", "false_negative", eid) in s and ("trajectory_miss", "false_positive", tid) in s


def test_synthetic_queue_has_all_three_kinds(con, cfg):
    errors.detect(con, cfg, END)
    kinds = {r[0] for r in con.execute("SELECT DISTINCT kind FROM error_reviews")}
    assert kinds == {"false_positive", "false_negative", "extraction_error"}


def test_detect_twice_adds_nothing(con, cfg):
    first = errors.detect(con, cfg, END)
    again = errors.detect(con, cfg, END)
    assert sum(first.values()) > 0 and sum(again.values()) == 0


def test_replay_higher_trend_threshold(con, cfg):
    # A patient whose only flag before an event is a trend flag: a higher trend threshold loses that flag.
    _patient(con, 972)
    for day, sbp in (("2026-05-01", 120), ("2026-06-01", 120), ("2026-07-01", 120),
                     ("2026-08-01", 142), ("2026-08-20", 143), ("2026-09-10", 144)):
        con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes) "
                    "VALUES (972, ?, 'chw', ?, 84, ?, '[]')", (day, sbp, f"{sbp}/84"))
    eid = con.execute("INSERT INTO events (patient_id, date, type, source) VALUES (972, '2026-09-25', 'stroke', 'clinic')").lastrowid
    higher = {"trend": {"rules": [{"sbp_rise_mmhg": 25, "consecutive_readings": 3}, {"sbp_rise_mmhg": 30, "over_readings": 4}]}}
    r = errors.replay(con, cfg, higher)
    assert r["flags_candidate"] < r["flags_now"]
    assert r["flags_by_rule_candidate"].get("trend", 0) < r["flags_by_rule_now"]["trend"]
    assert eid in [e["event_id"] for e in r["events_losing_prior_flag"]]
    assert r["seconds"] < 10


def test_summary_and_labels(con, cfg):
    errors.detect(con, cfg, END)
    s = errors.summary(con, cfg)
    assert s["per_rule"] and s["sensitivity_proxy"]["events"] > 0 and "low_yield_visits" in s
    rid = con.execute("SELECT id FROM error_reviews LIMIT 1").fetchone()[0]
    assert "error" in errors.label(con, rid, "confirmed_error", None, "Nurse A")  # needs a cause
    out = errors.label(con, rid, "confirmed_error", "trend_rule", "Nurse A")
    assert out["supervisor_label"] == "confirmed_error" and out["reviewed_by"] == "Nurse A"


def test_low_yield_visit(con, cfg):
    did = con.execute("INSERT INTO decisions (patient_id, date, state_json, suggestion, final_choice) "
                      "VALUES (2, '2026-09-20', '{}', 'visit_this_week', 'visit_this_week')").lastrowid
    con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, meds_taken, symptom_codes) "
                "VALUES (2, '2026-09-23', 'chw', 128, 80, '128/80', 'yes', '[]')")
    assert did in [v["decision_id"] for v in errors.low_yield_visits(con, cfg)]
