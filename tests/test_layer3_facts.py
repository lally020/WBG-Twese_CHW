"""PLAN.md 5b, Design B ("facts first"): context-code checks, the three-option mapping, the guardrail, the table,
and the switch between decide_mode values (the revert path)."""

from engine import decide, llm, models
from engine.common import dumps
from tests.conftest import END

TEXT = "BP taken right after she walked up the hill carrying water. Repeat after 10 minutes of rest: 138/86."


def _fields(codes):
    return {"sbp": 138, "dbp": 86, "meds_taken": None, "symptoms": [], "free_text_rest": "", "context_codes": codes}


def test_context_code_checks(cfg):
    ok, why = llm.check_extract(cfg, TEXT, _fields([{"code": "measured_after_exertion",
                                                     "quote": "walked up the hill carrying water"}]))
    assert why is None and ok["context_codes"][0]["code"] == "measured_after_exertion"
    assert llm.check_extract(cfg, TEXT, _fields([{"code": "feels_tired", "quote": "walked up the hill"}]))[0] is None
    bad, why = llm.check_extract(cfg, TEXT, _fields([{"code": "travelling", "quote": "went to Bujumbura"}]))
    assert bad is None and "does not appear" in why  # a quote not in the text sends the message to manual entry
    ok, _ = llm.check_extract(cfg, TEXT, _fields([{"code": "none", "quote": ""}]))
    assert ok["context_codes"] == []


def test_three_option_mapping_and_guardrail(cfg):
    assert decide._step(cfg, "routine", "step_up")[0] == "recheck_7_days"
    assert decide._step(cfg, "visit_this_week", "step_down")[0] == "recheck_7_days"
    assert decide._step(cfg, "visit_this_week", "keep")[0] == "visit_this_week"
    assert decide._step(cfg, "refer_clinic", "step_down")[0] == "refer_clinic"  # never down from refer
    assert decide._step(cfg, "refer_clinic", "step_up")[0] == "refer_clinic"  # top of the ladder
    assert decide._step(cfg, "routine", "step_down")[0] == "routine"


def test_table_decider(cfg):
    c = lambda *codes: [{"code": x, "quote": x} for x in codes]  # noqa: E731
    assert decide.table_decider(cfg, "routine", c("ran_out_of_pills"))[0] == "step_up"
    assert decide.table_decider(cfg, "visit_this_week", c("measured_after_exertion"))[0] == "keep"
    assert decide.table_decider(cfg, "visit_this_week", c("measured_after_exertion", "repeat_reading_normal"))[0] == "step_down"
    assert decide.table_decider(cfg, "visit_this_week", c("seen_at_clinic_recently", "side_effect"))[0] == "keep"
    assert decide.table_decider(cfg, "routine", [])[0] == "keep"


def _patient(con, pid, sbp=166, dbp=102):
    con.execute("INSERT INTO patients (id, sex, age, village, language, condition_codes) "
                "VALUES (?, 'female', 60, 'Ngozi', 'sw', '[\"hypertension\"]')", (pid,))
    for day, s, d in (("2026-07-01", 134, 84), ("2026-08-01", 135, 85), ("2026-09-01", 133, 84)):
        con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes) "
                    "VALUES (?, ?, 'chw', ?, ?, ?, '[]')", (pid, day, s, d, f"{s}/{d}"))
    codes = [{"code": "measured_after_exertion", "quote": "walked up the hill carrying water"},
             {"code": "repeat_reading_normal", "quote": "Repeat after 10 minutes of rest: 138/86"}]
    con.execute("INSERT INTO encounters (patient_id, date, source, sbp, dbp, bp_text, symptom_codes, chw_notes_text, "
                "context_codes_json) VALUES (?, '2026-10-02', 'chw', ?, ?, ?, '[]', ?, ?)",
                (pid, sbp, dbp, f"{sbp}/{dbp}", TEXT, dumps(codes)))


def _fake_julia(monkeypatch):
    def choose(cfg, state, question, options):
        if "keep" in options:  # Design B question
            probs = {"step_down": 0.9, "keep": 0.05, "step_up": 0.05} if "measured_after_exertion" in state \
                else {"keep": 0.9, "step_down": 0.05, "step_up": 0.05}
        else:  # Design A question
            probs = {a: 0.0 for a in options}
            probs["visit_this_week"] = 0.9
        return max(probs, key=probs.get), probs
    monkeypatch.setattr(models, "julia_choose", choose)


def test_facts_state_has_no_raw_text(con, cfg):
    _patient(con, 980)
    text, state = decide.build_state_facts(con, cfg, 980, END)
    assert "measured_after_exertion" in text and "Rule default: visit_this_week." in text
    assert TEXT not in text and "CHW notes" not in text  # only the quotes, never the raw note


def test_decide_mode_switch(con, cfg, monkeypatch):
    _fake_julia(monkeypatch)
    _patient(con, 981)
    cfg["julia"]["decide"] = True
    cfg["julia"]["decide_mode"] = "facts"
    b = decide.suggest(con, cfg, 981, END)
    assert b["suggestion"] == "recheck_7_days" and b["direction"] == "down"
    assert b["moved_by"]["code"] in ("measured_after_exertion", "repeat_reading_normal")
    cfg["julia"]["decide_mode"] = "table"
    t = decide.suggest(con, cfg, 981, END)
    assert t["suggestion"] == "recheck_7_days" and t["model_version"] == "rules+table-v1"
    cfg["julia"]["decide_mode"] = "actions"  # the revert: Design A, untouched
    a = decide.suggest(con, cfg, 981, END)
    assert "CHW notes:" in a["state_text"] and a["suggestion"] == "visit_this_week"


def test_emergency_bypasses_facts_mode(con, cfg, monkeypatch):
    called = []
    monkeypatch.setattr(models, "julia_choose", lambda *a, **k: called.append(1))
    cfg["julia"]["decide_mode"] = "facts"
    from tests.conftest import patients_by
    pid = patients_by(con, group="emergency_symptom")[0]
    assert decide.suggest(con, cfg, pid, END)["suggestion"] == "emergency_now" and called == []


def test_lexicon_adds_missed_codes_with_quotes(cfg):
    got = {c["code"]: c["quote"] for c in llm.lexicon_codes(cfg, "Vit seule, ne sait pas lire. Miguu imevimba.")}
    assert got["lives_alone"] == "Vit seul" and got["cannot_read"] == "ne sait pas lire" and got["side_effect"] == "imevimba"
    assert llm.lexicon_codes(cfg, "Home visit, no complaints.") == []
