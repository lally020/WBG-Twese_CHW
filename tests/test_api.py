"""Steps 5 and 13: every route answers, and the eight-step demo runs twice in a row through HTTP.
The LLM is replaced by a fixed answer so the test runs without Ollama."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from engine import api, llm
from tests.conftest import CONFIG

DEMO = "Habari. Leo presha yangu ni 138/88. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa."


@pytest.fixture
def client(db, monkeypatch, tmp_path):
    api.configure(db, CONFIG)
    fields = {"sbp": 138, "dbp": 88, "meds_taken": "partial", "symptoms": ["headache"], "free_text_rest": "",
              "context_codes": [{"code": "missed_doses", "quote": "nusu ya dawa"}]}
    monkeypatch.setattr(llm, "extract", lambda *a, **k: {"fields": fields, "valid": True, "reason": "ok", "model": "fake"})
    monkeypatch.setattr(llm, "summary", lambda con, cfg, pid: {"lines": ["a", "b", "c"], "source": "fake"})
    monkeypatch.setattr(llm, "reachable", lambda: False)
    from engine import server
    return TestClient(server.app)


def test_reads(client):
    assert client.get("/health").json() == {"status": "ok"}
    s = client.get("/status").json()
    assert s["approved"] is False and s["banner"]
    pats = client.get("/patients").json()
    assert pats["count"] == 60 and len(pats["patients"][0]["last_six_bp"]) <= 6
    assert client.get("/patients/1").json()["patient"]["village"] == "Kirundo"
    esc = client.get("/escalation").json()
    assert esc["patients"] and esc["protocol"]["text"]
    for path in ["/inbox", "/outgoing", "/followups", "/hours", "/metrics?month=2026-09", "/quiz/support",
                 "/phone/1", "/demo/script", "/languages", "/patients/1/trajectory"]:
        r = client.get(path)
        assert r.status_code == 200 and "error" not in r.json(), path
    assert "error" in client.get("/patients/9999").json()


def _demo(client):
    p1 = next(p for p in client.get("/patients").json()["patients"] if p["id"] == 1)
    assert p1["tier"] in ("low", "medium", None) and p1["latest_bp"]["sbp"] < 140  # borderline, under the goal
    r = client.post("/messages/incoming", json={"patient_id": 1, "text": DEMO}).json()
    assert r["routed_to"] == "inbox" and r["parsed"]["valid"]
    c = client.post(f"/messages/{r['message_id']}/confirm", json={"fields": r["parsed"]["fields"]}).json()
    # PLAN.md demo step 3: still under 140/90, the trend fires on the SMS, with the borderline flag and the headache.
    assert {"trend", "symptom", "borderline"} <= {f["type"] for f in c["flags"]}
    assert c["risk"]["tier"] == "high"
    assert c["suggestion"]["suggestion"] == "visit_this_week"  # missed_doses steps the rule default up (table)
    assert c["suggestion"]["moved_by"]["code"] == "missed_doses"
    assert c["quiz_question"]["topic"] == "danger_signs"
    o = client.post("/patients/1/confirm_decision", json={"final_choice": "recheck_7_days",
                                                         "override_reason": "patient travelling, weekly SMS continues"}).json()
    assert o["overridden"]
    client.post("/sync")
    a = client.post("/messages/incoming", json={"patient_id": 1, "text": "2"}).json()
    assert a["routed_to"] == "quiz" and a["quiz"]["correct"] == "no"
    assert client.post(f"/messages/{a['quiz']['correction_message_id']}/sent").json()["status"] == "sent"
    assert client.get("/phone/1").json()["messages"][-1]["kind"] == "correction"
    week = (date.today() + timedelta(days=7 - date.today().weekday())).isoformat()
    plan = client.get(f"/plan?chw=1&week_start={week}&hours=20&regenerate=true").json()
    assert next(r for r in plan["rows"] if r["patient_id"] == 1)["action"] == "visit"
    sms_row = next(r for r in plan["rows"] if r["action"] == "sms")
    client.post(f"/plan/{sms_row['id']}/update", json={"action": "visit"})
    assert client.post("/plan/approve", json={"chw_id": 1, "week_start": week}).json()["approved"]
    return client.get("/languages").json()


def test_symptom_not_swallowed_by_pending_quiz(client):
    client.post("/quiz/next", json={"patient_id": 1, "channel": "sms"})
    r = client.post("/messages/incoming", json={"patient_id": 1, "text": "uso umelegea upande mmoja"}).json()
    assert r["routed_to"].startswith("inbox") and "parsed" in r


def test_demo_twice(client, monkeypatch):
    # The review step trains models; skip saving files outside the temp folder by checking the routes only.
    _demo(client)
    client.post("/demo/reset", json={"seed": 1})
    langs = _demo(client)
    assert {l["lang"] for l in langs["languages"]} == {"en", "fr", "sw", "rn"}


def test_protocol_by_language(client):
    en = client.get("/escalation?lang=en").json()["protocol"]
    fr = client.get("/escalation?lang=fr").json()["protocol"]
    sw = client.get("/escalation?lang=sw").json()["protocol"]
    assert en["lang"] == "en" and "When this box appears" in en["text"]
    assert fr["lang"] == "fr" and "Quand cette alerte" in fr["text"]
    assert sw["lang"] == "en"  # not in the file: English


def test_goal_override_needs_supervisor_pin(client):
    assert "id" in api.add_user("Nurse A", "supervisor", "4821")
    assert "id" in api.add_user("CHW B", "chw", "1357")
    body = {"goal_override": "high_risk", "pin": "1357"}
    assert "only a supervisor" in client.post("/patients/2/goal_override", json=body).json()["error"]
    assert "not recognised" in client.post("/patients/2/goal_override", json={**body, "pin": "0000"}).json()["error"]
    ok = client.post("/patients/2/goal_override", json={**body, "pin": "4821"}).json()
    assert ok["goal_override"] == "high_risk" and ok["goal"]["id"] == "bp_goal_high_risk" and ok["set_by"] == "Nurse A"
    assert client.get("/patients/2").json()["patient"]["goal_override"] == "high_risk"


def test_process_pending_batch(client):
    for text in ("presha 150/95", "asante"):
        client.post("/messages/incoming", json={"patient_id": 2, "text": text, "auto_process": False})
    out = client.post("/inbox/process", json={"limit": 10}).json()
    assert out["processed"] == 2 and out["still_waiting"] == 0


def test_error_reviews_routes_need_supervisor_pin(client):
    api.add_user("Nurse A", "supervisor", "4821")
    api.add_user("CHW B", "chw", "1357")
    found = client.post("/errors/detect", json={}).json()
    assert found["total"] > 0
    rid = client.get("/errors?label=pending").json()["reviews"][0]["id"]
    body = {"supervisor_label": "correct_call", "pin": "1357"}
    assert "only a supervisor" in client.post(f"/errors/{rid}/label", json=body).json()["error"]
    ok = client.post(f"/errors/{rid}/label", json={**body, "pin": "4821"}).json()
    assert client.get("/errors/summary?month=2026-10").json()["month"] == "2026-10"
    rep = client.post("/errors/replay", json={"changes": {"trend": {"rules": [{"sbp_rise_mmhg": 7, "consecutive_readings": 3}]}}}).json()
    assert rep["contacts_flagged_candidate"] >= rep["contacts_flagged_now"]
    assert ok["supervisor_label"] == "correct_call" and ok["reviewed_by"] == "Nurse A"


def test_referral_outcome(client):
    client.post("/patients/1/decide")
    client.post("/patients/1/confirm_decision", json={"final_choice": "refer_clinic"})
    out = client.post("/patients/1/referral_outcome", json={"outcome": "admitted"}).json()
    assert out["outcome"] == "admitted" and out["event_id"]
    assert "error" in client.post("/patients/1/referral_outcome", json={"outcome": "fine"}).json()


def test_reset_clears_error_reviews(client):
    client.post("/errors/detect", json={})
    client.post("/demo/reset", json={"seed": 1})
    assert client.get("/errors").json()["reviews"] == []
