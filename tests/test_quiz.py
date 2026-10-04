"""Step 10: two answers by SMS; understanding scores; a wrong answer comes back in 7 days;
low danger-sign understanding moves a patient up the plan."""

from engine import quiz, risk
from engine.common import add_days, iso


def test_demo_patient_gets_danger_signs_first(con, cfg):
    q = quiz.next_question(con, cfg, 1)
    assert q["topic"] == "danger_signs" and q["lang"] == "sw"
    assert q["message_id"] and "1)" in q["text"]


def test_two_answers_and_understanding(con, cfg):
    q1 = quiz.next_question(con, cfg, 1)
    bank = {q["id"]: q for q in quiz.content.quiz_bank()["questions"]}
    right = bank[q1["question_id"]]["correct"]
    a1 = quiz.record_answer(con, cfg, 1, q1["question_id"], str(right))
    assert a1["correct"] == "yes" and a1["correction_message_id"] is None
    q2 = quiz.next_question(con, cfg, 1)
    wrong = bank[q2["question_id"]]["correct"] % 3 + 1
    a2 = quiz.record_answer(con, cfg, 1, q2["question_id"], str(wrong))
    assert a2["correct"] == "no" and a2["correction_message_id"]
    assert a2["next_due"] == add_days(iso(None), cfg["quiz"]["repeat_days_wrong"])
    u = quiz.understanding(con, cfg, 1)
    assert u[q2["topic"]] is not None


def test_unclear_reply(con, cfg):
    q = quiz.next_question(con, cfg, 1)
    assert quiz.record_answer(con, cfg, 1, q["question_id"], "sijui kabisa")["correct"] == "unclear"


def test_low_danger_signs_raises_risk(con, cfg):
    before = risk.score_patient(con, cfg, 1)["score"]
    q = quiz.next_question(con, cfg, 1)
    assert q["topic"] == "danger_signs"
    bank = {x["id"]: x for x in quiz.content.quiz_bank()["questions"]}
    quiz.record_answer(con, cfg, 1, q["question_id"], str(bank[q["question_id"]]["correct"] % 3 + 1))
    after = risk.score_patient(con, cfg, 1)
    assert after["score"] == before + cfg["risk"]["points"]["low_understanding"]
    assert "low_understanding" in after["components"]
    assert any(p["patient_id"] == 1 for p in quiz.support_needed(con, cfg)["patients"])
