"""Step 7: reminders appear in the outgoing queue; the weekly hours table returns."""

from engine import followups, hours
from tests.conftest import END


def test_reminder_queued_before_appointment(con, cfg):
    con.execute("INSERT INTO followups (patient_id, due_date, kind, status) VALUES (2, '2026-10-05', 'clinic_appointment', 'due')")
    ids = followups.queue_reminders(con, cfg, END)
    q = followups.outgoing_queue(con)
    assert ids and any(m["kind"] == "reminder" and m["patient_id"] == 2 and "2026-10-05" in m["text"] for m in q)
    # Not queued twice.
    again = followups.queue_reminders(con, cfg, END)
    assert not set(again) & set(ids)


def test_follow_up_after_visit(con, cfg):
    con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp) VALUES (3, '2026-09-30', 'chw', 140, 90)")
    followups.queue_reminders(con, cfg, END)
    assert any(m["kind"] == "follow_up" and m["patient_id"] == 3 for m in followups.outgoing_queue(con))


def test_mark_sent(con, cfg):
    con.execute("INSERT INTO followups (patient_id, due_date, kind, status) VALUES (2, '2026-10-04', 'clinic_appointment', 'due')")
    mid = followups.queue_reminders(con, cfg, END)[0]
    assert followups.mark_sent(con, mid)["status"] == "sent"
    assert mid not in [m["id"] for m in followups.outgoing_queue(con)]


def test_hours_table(con, cfg):
    out = hours.hours(con, cfg, 1, "2026-09-21")
    acts = {a["activity"]: a for a in out["by_activity"]}
    assert set(acts) == {"visit", "sms", "travel", "admin"}
    assert out["total_hours"] > 0
    assert len(out["villages"]) == 6 and all(v["visit"] > 0 for v in out["villages"])


def test_log_time(con):
    res = hours.log_time(con, 1, "visit", 1, "2026-10-03T09:00:00", "2026-10-03T09:30:00")
    assert res["village"] == "Kirundo" and res["minutes"] == 30
    assert "error" in hours.log_time(con, 1, "visit", 1, "2026-10-03T09:30:00", "2026-10-03T09:00:00")


def test_followups_after_each_decision(con, cfg):
    made = followups.create_after_decision(con, cfg, 2, "visit_this_week", as_of=END)
    assert [(f["kind"], f["due_date"]) for f in made] == [("chw_visit", "2026-10-10"), ("sms_check", "2026-10-17")]
    made = followups.create_after_decision(con, cfg, 2, "refer_clinic", as_of=END)
    assert [(f["kind"], f["due_date"]) for f in made] == [("clinic_appointment", "2026-10-10"), ("sms_check", "2026-10-13")]
    urgent = followups.create_after_decision(con, cfg, 2, "refer_clinic", urgent=True, as_of=END)
    assert [(f["kind"], f["due_date"]) for f in urgent] == [("clinic_appointment", "2026-10-05"), ("sms_check", "2026-10-08")]
    assert urgent[1]["purpose"] == "confirm attendance"
    assert [f["kind"] for f in followups.create_after_decision(con, cfg, 2, "emergency_now", as_of=END)] == ["chw_visit"]


def test_urgent_referral_from_decision(con, cfg):
    """A severe reading with no symptom: the decision carries urgent_days, so the appointment is in 2 days."""
    from engine import decide
    con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes) "
                "VALUES (2, '2026-10-03', 'chw', 184, 112, '184/112', '[]')")
    out = decide.decide(con, cfg, 2, END)
    assert out["suggestion"] == "refer_clinic"
    res = decide.confirm(con, cfg, 2, "refer_clinic", as_of=END)
    assert res["urgent"] and res["followups"][0]["due_date"] == "2026-10-05"


def test_borderline_weekly_watch(con, cfg):
    from engine import signals
    con.execute("INSERT INTO patients (id, sex, age, village, language, condition_codes) "
                "VALUES (990, 'female', 60, 'Ngozi', 'sw', '[\"hypertension\"]')")
    for i, s in enumerate([124, 125, 126, 131, 133, 136]):
        con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes) "
                    "VALUES (990, ?, 'chw', ?, 82, ?, '[]')", (f"2026-0{4 + i}-01", s, f"{s}/82"))
    flags = signals.compute(con, cfg, 990, END)["flags"]
    assert followups.borderline_watch(con, cfg, 990, flags, END) == "started"
    assert followups.borderline_watch(con, cfg, 990, flags, END) is None  # one open watch per patient
    asks = followups.queue_borderline_asks(con, cfg, "2026-10-10")
    assert len(asks) == 1 and con.execute("SELECT kind FROM messages WHERE id = ?", (asks[0],)).fetchone()[0] == "follow_up"
    assert followups.queue_borderline_asks(con, cfg, "2026-10-10") == []  # next ask is a week later
    for d, s in (("2026-10-11", 124), ("2026-10-12", 122)):  # two readings in a row below the band
        con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes) "
                    "VALUES (990, ?, 'sms', ?, 78, ?, '[]')", (d, s, f"{s}/78"))
    assert followups.borderline_watch(con, cfg, 990, [], "2026-10-12") == "stopped"
