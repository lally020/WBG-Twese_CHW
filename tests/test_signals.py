"""Step 3: thresholds (with and without symptoms), the two trend rules, symptom actions by BP goal."""

from engine import signals
from tests.conftest import END, patients_by


def _add(con, pid, day, sbp, dbp, symptoms="[]", source="chw"):
    con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (pid, day, source, sbp, dbp, f"{sbp}/{dbp}", symptoms))


def _new_patient(con, pid, goal_override=None):
    con.execute("INSERT INTO patients (id, sex, age, village, language, condition_codes, goal_override) "
                "VALUES (?, 'female', 60, 'Ngozi', 'sw', '[\"hypertension\"]', ?)", (pid, goal_override))


def _series(con, pid, values, start_month=3):
    for i, s in enumerate(values):
        _add(con, pid, f"2026-{start_month + i:02d}-01" if start_month + i <= 9 else f"2026-09-{i:02d}", s, 78)


def _flags(con, cfg, pid, kind):
    return [f for f in signals.compute(con, cfg, pid, END)["flags"] if f["type"] == kind]


# ---------- severe thresholds ----------

def test_severe_with_headache_is_emergency(con, cfg):
    _new_patient(con, 902)
    _add(con, 902, "2026-09-01", 140, 88)
    _add(con, 902, "2026-10-01", 180, 115, '["headache"]')
    f = _flags(con, cfg, 902, "threshold")[0]
    assert f["emergency"] == "yes" and f["action"] == "emergency_now"
    assert "headache" in f["reason_text"]


def test_severe_without_symptom_is_urgent_referral(con, cfg):
    _new_patient(con, 903)
    _add(con, 903, "2026-10-01", 180, 115)
    f = _flags(con, cfg, 903, "threshold")[0]
    assert f["emergency"] == "no" and f["action"] == "refer_clinic"
    assert f["urgent_days"] == 2


def test_severe_with_symptom_in_sms_the_day_before(con, cfg):
    _new_patient(con, 904)
    _add(con, 904, "2026-09-30", None, None, '["dizziness"]', source="sms")
    _add(con, 904, "2026-10-01", 182, 112)
    assert _flags(con, cfg, 904, "threshold")[0]["emergency"] == "yes"


def test_fast_sign_is_emergency_at_any_bp(con, cfg):
    _new_patient(con, 905)
    _add(con, 905, "2026-09-01", 128, 80)
    _add(con, 905, "2026-10-01", 126, 78, '["face_droop"]')
    flags = signals.compute(con, cfg, 905, END)["flags"]
    assert any(f["type"] == "symptom" and f["emergency"] == "yes" for f in flags)


def test_grade2_still_visit(con, cfg):
    _new_patient(con, 906)
    _add(con, 906, "2026-10-01", 165, 95)
    f = _flags(con, cfg, 906, "threshold")[0]
    assert f["action"] == "visit_this_week" and f["emergency"] == "no"


# ---------- trend rules ----------

def test_trend_rule_three_consecutive(con, cfg):
    _new_patient(con, 910)
    _series(con, 910, [130, 130, 130, 141, 142, 143])
    f = _flags(con, cfg, 910, "trend")
    assert len(f) == 1 and "each of the last 3 readings" in f[0]["reason_text"]
    assert f[0]["value"] == "+11"


def test_trend_rule_four_reading_rise(con, cfg):
    _new_patient(con, 911)
    _series(con, 911, [130, 130, 130, 138, 150, 139, 160])  # last three are not all +10, the last four average +17
    f = _flags(con, cfg, 911, "trend")
    assert len(f) == 1 and "last 4 readings" in f[0]["reason_text"] and "each of" not in f[0]["reason_text"]


def test_trend_never_fires_on_one_reading(con, cfg):
    _new_patient(con, 912)
    _series(con, 912, [130, 131, 129, 133, 132, 175])
    assert _flags(con, cfg, 912, "trend") == []


def test_stable_patient_no_flags(con, cfg):
    _new_patient(con, 913)
    _series(con, 913, [124, 126, 123, 125, 124, 126, 123])
    assert signals.compute(con, cfg, 913, END)["flags"] == []


# ---------- borderline (PLAN.md step 3, 4 October) ----------

def test_borderline_band(con, cfg):
    _new_patient(con, 930)
    _series(con, 930, [124, 125, 126, 131, 133, 136])  # three in a row in 130-139, never at 140
    f = _flags(con, cfg, 930, "borderline")
    assert len(f) == 1 and "3 readings in a row" in f[0]["reason_text"] and "130-139" in f[0]["reason_text"]
    assert f[0]["emergency"] == "no" and f[0]["action"] == "recheck_7_days"


def test_borderline_crossings(con, cfg):
    _new_patient(con, 931)
    _series(con, 931, [124, 125, 126, 142, 128, 143])  # above, below, above the goal: 3 crossings
    f = _flags(con, cfg, 931, "borderline")
    assert len(f) == 1 and "crossed the goal" in f[0]["reason_text"]


def test_borderline_needs_enough_readings_and_no_threshold(con, cfg):
    _new_patient(con, 932)
    _series(con, 932, [132, 134, 136])  # only the baseline readings
    assert _flags(con, cfg, 932, "borderline") == []
    _new_patient(con, 933)
    _series(con, 933, [124, 132, 134, 136, 165])  # a threshold flag: no borderline alongside it
    assert _flags(con, cfg, 933, "threshold") and _flags(con, cfg, 933, "borderline") == []


def test_borderline_band_uses_the_high_risk_goal(con, cfg):
    _new_patient(con, 934, goal_override="high_risk")  # goal 130/80: band 120-129 systolic
    for i, (s, d) in enumerate([(114, 70), (115, 71), (116, 72), (122, 74), (124, 74), (126, 75)]):
        _add(con, 934, f"2026-0{3 + i}-01", s, d)
    f = _flags(con, cfg, 934, "borderline")
    assert len(f) == 1 and "130/80" in f[0]["reason_text"]


# ---------- non-emergency symptoms and the BP goal ----------

def test_dizziness_always_visit_even_at_goal(con, cfg):
    _new_patient(con, 920)
    _add(con, 920, "2026-10-01", 126, 78, '["dizziness"]')
    f = _flags(con, cfg, 920, "symptom")[0]
    assert f["action"] == "visit_this_week" and f["emergency"] == "no"


def test_headache_at_goal_recheck_above_goal_visit(con, cfg):
    _new_patient(con, 921)
    _add(con, 921, "2026-10-01", 132, 82, '["headache"]')
    assert _flags(con, cfg, 921, "symptom")[0]["action"] == "recheck_7_days"
    _new_patient(con, 922)
    _add(con, 922, "2026-10-01", 148, 92, '["headache"]')
    assert _flags(con, cfg, 922, "symptom")[0]["action"] == "visit_this_week"


def test_high_risk_goal_is_used(con, cfg):
    _new_patient(con, 923, goal_override="high_risk")  # goal 130/80, so 132/82 is above goal
    _add(con, 923, "2026-10-01", 132, 82, '["headache"]')
    out = signals.compute(con, cfg, 923, END)
    assert out["goal"]["id"] == "bp_goal_high_risk"
    assert [f for f in out["flags"] if f["type"] == "symptom"][0]["action"] == "visit_this_week"


def test_synthetic_groups(con, cfg):
    mods = {r[0] for r in con.execute("SELECT patient_id FROM synthetic_truth WHERE text_modifier != 'none'")}
    mods |= {r[0] for r in con.execute("SELECT id FROM patients WHERE status = 'former'")}  # history only
    stable_flagged = sum(bool([f for f in signals.compute(con, cfg, p, END)["flags"] if f["type"] != "who_risk"])
                         for p in patients_by(con, "stable") if p not in mods)
    det = patients_by(con, d30="yes")
    assert stable_flagged <= 2
    border = patients_by(con, "borderline")
    assert len(border) == 10
    assert sum(any(f["type"] == "borderline" for f in signals.compute(con, cfg, p, END)["flags"]) for p in border) >= 7
    assert all(signals.compute(con, cfg, p, END)["flags"] for p in det)
