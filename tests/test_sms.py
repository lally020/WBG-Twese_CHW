"""Step 6: the regex, the four LLM checks, confirmation, and the record import.
The LLM itself is replaced by fixed answers here, so the tests run without Ollama."""

import os

from engine import llm, sms
from tests.conftest import ROOT

DEMO = "Habari. Leo presha yangu ni 150/95. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa."


def test_regex():
    assert sms.regex_bp("presha 150/95 leo") == (150, 95)
    assert sms.regex_bp("150 na 95") == (150, 95)
    assert sms.regex_bp("ma tension 160 sur 100") == (160, 100)
    assert sms.regex_bp("on 2026-10-03 I felt fine") is None
    assert sms.regex_bp("nina kichwa") is None


def test_checks_accept_clean(cfg):
    data = {"sbp": 150, "dbp": 95, "meds_taken": "partial", "symptoms": ["headache"], "free_text_rest": "",
            "context_codes": []}
    fields, reason = llm.check_extract(cfg, DEMO, data)
    assert reason is None and fields["sbp"] == 150


def test_checks_reject_invented_number(cfg):
    data = {"sbp": 160, "dbp": 95, "meds_taken": "partial", "symptoms": [], "free_text_rest": "", "context_codes": []}
    fields, reason = llm.check_extract(cfg, DEMO, data)
    assert fields is None and "160" in reason


def test_checks_reject_unknown_symptom_and_schema(cfg):
    bad_sym = {"sbp": 150, "dbp": 95, "meds_taken": None, "symptoms": ["fever"], "free_text_rest": "", "context_codes": []}
    assert llm.check_extract(cfg, DEMO, bad_sym)[0] is None
    assert llm.check_extract(cfg, DEMO, {"sbp": 150})[0] is None


def test_medication_checks(cfg):
    text = "Amlodipine 5 mg once daily. Reduce salt."
    ok = {"medications": [{"name": "amlodipine", "dose_text": "5 mg once daily"}]}
    assert llm.check_medications(cfg, text, ok)[1] is None
    invented = {"medications": [{"name": "enalapril", "dose_text": "10 mg"}]}
    assert llm.check_medications(cfg, text, invented)[0] is None


def _incoming(con, text):
    cur = con.execute("INSERT INTO messages (patient_id, direction, kind, lang, text, received_at) "
                      "VALUES (1, 'in', 'other', 'sw', ?, '2026-10-03T08:00:00')", (text,))
    con.commit()
    return cur.lastrowid


def test_process_and_confirm(con, cfg, monkeypatch):
    fields = {"sbp": 150, "dbp": 95, "meds_taken": "partial", "symptoms": ["headache"], "free_text_rest": ""}
    monkeypatch.setattr(llm, "extract", lambda *a, **k: {"fields": fields, "valid": True, "reason": "ok", "model": "fake"})
    mid = _incoming(con, DEMO)
    out = sms.process(con, cfg, mid)
    assert out["valid"] and not out["needs_manual"] and out["fields"]["sbp"] == 150
    n_before = con.execute("SELECT COUNT(*) FROM encounters").fetchone()[0]
    res = sms.confirm(con, cfg, mid, fields)
    assert res["encounter_id"]
    assert con.execute("SELECT COUNT(*) FROM encounters").fetchone()[0] == n_before + 1


def test_regex_and_llm_disagree_goes_to_manual(con, cfg, monkeypatch):
    fields = {"sbp": 95, "dbp": 150, "meds_taken": None, "symptoms": [], "free_text_rest": ""}
    monkeypatch.setattr(llm, "extract", lambda *a, **k: {"fields": fields, "valid": True, "reason": "ok", "model": "fake"})
    out = sms.process(con, cfg, _incoming(con, "150 na 95"))
    assert out["needs_manual"]


def test_llm_down_keeps_regex_numbers(con, cfg, monkeypatch):
    monkeypatch.setattr(llm, "extract", lambda *a, **k: {"fields": None, "valid": False, "reason": "down", "model": None})
    out = sms.process(con, cfg, _incoming(con, "presha 150/95"))
    assert out["needs_manual"] and out["fields"]["sbp"] == 150


def test_import_sample_csv(con):
    from data.import_records import import_csv
    res = import_csv(con, os.path.join(ROOT, "data", "sample_import.csv"))
    assert res["patients_added"] == 2 and res["encounters_added"] == 5
    assert len(res["rows_not_read"]) == 1


def test_import_goal_override(con, tmp_path):
    from data.import_records import import_csv
    path = tmp_path / "goal.csv"
    path.write_text("patient_ref,sex,age,village,language,date,source,bp,positive_results,goal_override\n"
                    "G-1,F,63,Ngozi,sw,2026-09-01,clinic,150/95,hypertension,high_risk\n"
                    "G-2,M,50,Ngozi,sw,2026-09-01,clinic,140/90,hypertension,\n"
                    "G-3,M,50,Ngozi,sw,2026-09-01,clinic,140/90,hypertension,very_high\n")
    res = import_csv(con, str(path))
    assert res["patients_added"] == 2 and len(res["rows_not_read"]) == 1
    goals = [r[0] for r in con.execute("SELECT goal_override FROM patients ORDER BY id DESC LIMIT 2")]
    assert goals == [None, "high_risk"]
