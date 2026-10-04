"""Step 4: rules first, then Julia-1 behind the one-step guardrail (PLAN.md step 4).
Julia-1 is replaced by a stand-in whose answer depends on the text, so these tests are deterministic."""

import pytest

from engine import decide, models
from tests.conftest import END, patients_by


@pytest.fixture(autouse=True)
def design_a(cfg):
    """These are the Design A tests: pin decide_mode to actions with Julia-1 on (the default is the table)."""
    cfg["julia"]["decide_mode"] = "actions"
    cfg["julia"]["decide"] = True


def _patient(con, pid, sbp, dbp, note=None, message=None, symptoms="[]"):
    con.execute("INSERT INTO patients (id, sex, age, village, language, condition_codes) "
                "VALUES (?, 'female', 60, 'Ngozi', 'sw', '[\"hypertension\"]')", (pid,))
    for day, s, d in (("2026-07-01", 124, 78), ("2026-08-01", 125, 79), ("2026-09-01", 123, 78)):
        con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes) "
                    "VALUES (?, ?, 'chw', ?, ?, ?, '[]')", (pid, day, s, d, f"{s}/{d}"))
    con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes, chw_notes_text) "
                "VALUES (?, '2026-10-02', 'chw', ?, ?, ?, ?, ?)", (pid, sbp, dbp, f"{sbp}/{dbp}", symptoms, note))
    if message:
        con.execute("INSERT INTO messages (patient_id, direction, kind, lang, text, received_at) "
                    "VALUES (?, 'in', 'other', 'sw', ?, '2026-10-02T18:00:00')", (pid, message))


def _fake_julia(monkeypatch, rule):
    """rule(state_text) -> {action: probability}. Stands in for Julia-1."""
    def choose(cfg, state, question, options):
        probs = {a: 0.0 for a in options}
        probs.update(rule(state))
        return max(probs, key=probs.get), probs
    monkeypatch.setattr(models, "julia_choose", choose)


def test_emergency_bypass(con, cfg, monkeypatch):
    called = []
    monkeypatch.setattr(models, "julia_choose", lambda *a, **k: called.append(1))
    pid = patients_by(con, group="emergency_symptom")[0]
    out = decide.suggest(con, cfg, pid, END)
    assert out["suggestion"] == "emergency_now" and out["model_version"].startswith("none")
    assert called == []


def test_rules_only_when_decide_false(con, cfg, monkeypatch):
    cfg["julia"]["decide"] = False
    _fake_julia(monkeypatch, lambda s: {"routine": 0.95})
    _patient(con, 960, 166, 102, note="BP taken right after she walked up the hill.")
    out = decide.suggest(con, cfg, 960, END)
    assert out["suggestion"] == out["rule_default"] == "visit_this_week" and "rules only" in out["reason_text"]


def test_step_down_on_exertion_note(con, cfg, monkeypatch):
    _fake_julia(monkeypatch, lambda s: {"recheck_7_days": 0.9, "visit_this_week": 0.05} if "walked up the hill" in s
                else {"visit_this_week": 0.9, "recheck_7_days": 0.05})
    note = "BP taken right after she walked up the hill carrying water. Repeat after 10 minutes of rest: 138/86."
    _patient(con, 961, 166, 102, note=note)
    out = decide.suggest(con, cfg, 961, END)
    assert out["rule_default"] == "visit_this_week" and out["suggestion"] == "recheck_7_days"
    assert out["direction"] == "down" and out["moved_by"]["text"] == note
    assert "walked up the hill" in out["reason_text"]


def test_step_up_on_pills_message(con, cfg, monkeypatch):
    _fake_julia(monkeypatch, lambda s: {"recheck_7_days": 0.9, "routine": 0.05} if "ziliisha" in s
                else {"routine": 0.9, "recheck_7_days": 0.05})
    _patient(con, 962, 126, 80, message="Dawa zangu ziliisha siku kumi zilizopita, sijapata nyingine.")
    out = decide.suggest(con, cfg, 962, END)
    assert out["rule_default"] == "routine" and out["suggestion"] == "recheck_7_days" and out["direction"] == "up"
    assert out["moved_by"]["kind"] == "patient message"


def test_clamp_two_steps(con, cfg, monkeypatch):
    _fake_julia(monkeypatch, lambda s: {"refer_clinic": 0.95} if "swollen" in s else {"routine": 0.95})
    _patient(con, 963, 126, 80, note="Feet and ankles swollen since the new tablets started.")
    out = decide.suggest(con, cfg, 963, END)
    assert out["rule_default"] == "routine" and out["suggestion"] == "recheck_7_days"
    assert "clamped" in out["guardrail"]


def test_no_step_down_from_refer_clinic(con, cfg, monkeypatch):
    _fake_julia(monkeypatch, lambda s: {"routine": 0.95} if "feels fine" in s else {"refer_clinic": 0.95})
    _patient(con, 964, 184, 112, note="Patient says she feels fine today.")  # severe, no symptom: refer_clinic
    out = decide.suggest(con, cfg, 964, END)
    assert out["rule_default"] == "refer_clinic" and out["suggestion"] == "refer_clinic"
    assert not out["moved"]


def test_move_kept_only_when_text_caused_it(con, cfg, monkeypatch):
    _fake_julia(monkeypatch, lambda s: {"recheck_7_days": 0.9})  # moves whatever the text says
    _patient(con, 965, 126, 80, note="Home visit, all well.")
    out = decide.suggest(con, cfg, 965, END)
    assert out["suggestion"] == "routine" and "no note or message caused" in out["guardrail"]


def test_low_confidence_gives_not_sure(con, cfg, monkeypatch):
    _fake_julia(monkeypatch, lambda s: {"routine": 0.4, "recheck_7_days": 0.35, "visit_this_week": 0.25})
    _patient(con, 966, 136, 86)
    assert decide.suggest(con, cfg, 966, END)["suggestion"] == "not_sure"


def test_empty_state_not_sure(con, cfg):
    con.execute("INSERT INTO patients (id, sex, age, village, language, condition_codes) VALUES "
                "(950, 'male', 50, 'Gitega', 'fr', '[\"hypertension\"]')")
    assert decide.suggest(con, cfg, 950, END)["suggestion"] == "not_sure"


def test_state_has_no_bare_reading(con, cfg):
    _patient(con, 967, 126, 80, note="Home visit.")
    text, state = decide.build_state(con, cfg, 967, END)
    assert "126/80" not in text  # no flag interprets this reading, so it does not appear
    assert "Rule default: routine." in text and "CHW notes:" in text


def test_confirm_stores_override_and_followup(con, cfg):
    decide.decide(con, cfg, 1, END)
    res = decide.confirm(con, cfg, 1, "recheck_7_days", "patient travelling", END)
    assert res["final_choice"] == "recheck_7_days"
    assert [f["kind"] for f in res["followups"]] == ["sms_check"]
    row = con.execute("SELECT * FROM decisions WHERE id = ?", (res["decision_id"],)).fetchone()
    assert row["config_version"] == cfg["config_version"] and row["model_version"]
