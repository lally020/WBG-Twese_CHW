"""FastAPI wrapper around engine/api.py. No logic here: one route per api function.

Start:  uvicorn engine.server:app --host 127.0.0.1 --port 8000
Docs:   http://127.0.0.1:8000/docs  (the live contract for the frontend team)
"""

import os
from typing import Optional

from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine import api

app = FastAPI(title="TWESE CHW AI engine", version="1.0",
              description="Offline engine for CHW hypertension and stroke-prevention support. All data is synthetic.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- request bodies ----------

class Incoming(BaseModel):
    patient_id: int
    text: str
    lang: Optional[str] = None
    auto_process: bool = True


class Fields(BaseModel):
    sbp: Optional[int] = None
    dbp: Optional[int] = None
    meds_taken: Optional[str] = None
    symptoms: list[str] = []
    free_text_rest: Optional[str] = ""


class Confirm(BaseModel):
    fields: Fields


class ConfirmDecision(BaseModel):
    final_choice: str
    override_reason: str = ""


class PlanUpdate(BaseModel):
    action: str


class PlanApprove(BaseModel):
    chw_id: int = 1
    week_start: str


class QuizNext(BaseModel):
    patient_id: int
    channel: str = "sms"


class QuizAnswer(BaseModel):
    patient_id: int
    question_id: str
    reply_text: str


class TimeEntry(BaseModel):
    chw_id: int = 1
    activity: str
    patient_id: Optional[int] = None
    start: str
    end: str


class Feedback(BaseModel):
    who: str
    target: str
    value: str
    note: str = ""


class GoalOverride(BaseModel):
    goal_override: Optional[str] = None
    pin: str


class ReviewLabel(BaseModel):
    supervisor_label: str
    cause_code: Optional[str] = None
    pin: str


class Replay(BaseModel):
    changes: dict


class ReferralOutcome(BaseModel):
    outcome: str


class ImportCsv(BaseModel):
    csv_text: str


# ---------- reads ----------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    return api.status()


@app.get("/languages")
def languages():
    return api.languages()


@app.get("/patients")
def list_patients(filter_text: Optional[str] = None, village: Optional[str] = None, tier: Optional[str] = None,
                  chw: Optional[int] = None):
    return api.list_patients(filter_text, village, tier, chw)


@app.get("/patients/{patient_id}")
def patient_detail(patient_id: int, include_summary: bool = True):
    return api.patient_detail(patient_id, include_summary)


@app.get("/patients/{patient_id}/summary")
def summary(patient_id: int):
    return api.summary(patient_id)


@app.get("/patients/{patient_id}/trajectory")
def trajectory(patient_id: int, min_events: Optional[int] = None):
    return api.trajectory(patient_id, min_events)


@app.get("/escalation")
def escalation_list(lang: str = "en"):
    return api.escalation_list(lang)


@app.get("/protocol")
def emergency_protocol(lang: str = "en"):
    return api.emergency_protocol(lang)


@app.get("/inbox")
def inbox(status: str = "all"):
    return api.inbox(status)


@app.get("/outgoing")
def outgoing_queue():
    return api.outgoing_queue()


@app.get("/followups")
def followups(patient_id: Optional[int] = None):
    return api.followups(patient_id)


@app.get("/plan")
def plan_week(chw: int = 1, week_start: Optional[str] = None, hours: Optional[float] = None, regenerate: bool = False):
    return api.plan_week(chw, week_start, hours, regenerate)


@app.get("/hours")
def hours(chw: int = 1, week_start: Optional[str] = None):
    return api.hours(chw, week_start)


@app.get("/metrics")
def metrics(month: Optional[str] = None):
    return api.metrics(month)


@app.get("/quiz/support")
def support_needed():
    return api.support_needed()


@app.get("/phone/{patient_id}")
def phone_thread(patient_id: int):
    return api.phone_thread(patient_id)


@app.get("/demo/script")
def demo_script():
    return api.demo_script()


# ---------- writes ----------

@app.post("/messages/incoming")
def simulate_incoming(body: Incoming):
    return api.simulate_incoming(body.patient_id, body.text, body.lang, body.auto_process)


@app.post("/inbox/process")
def process_pending(limit: int = Body(100, embed=True)):
    return api.process_pending(limit)


@app.post("/messages/{message_id}/process")
def process_message(message_id: int):
    return api.process_message(message_id)


@app.post("/messages/{message_id}/confirm")
def confirm_fields(message_id: int, body: Confirm):
    return api.confirm_fields(message_id, body.fields.model_dump())


@app.post("/messages/{message_id}/sent")
def mark_sent(message_id: int):
    return api.mark_sent(message_id)


@app.post("/sync")
def sync():
    return api.sync()


@app.post("/patients/{patient_id}/decide")
def decide(patient_id: int):
    return api.decide(patient_id)


@app.post("/patients/{patient_id}/confirm_decision")
def confirm_decision(patient_id: int, body: ConfirmDecision):
    return api.confirm_decision(patient_id, body.final_choice, body.override_reason)


@app.post("/risk/run")
def risk_all(date: Optional[str] = Body(None, embed=True)):
    return api.risk_all(date)


@app.post("/plan/{plan_id}/update")
def update_plan(plan_id: int, body: PlanUpdate):
    return api.update_plan(plan_id, body.action)


@app.post("/plan/approve")
def approve_plan(body: PlanApprove):
    return api.approve_plan(body.chw_id, body.week_start)


@app.post("/quiz/next")
def next_question(body: QuizNext):
    return api.next_question(body.patient_id, body.channel)


@app.post("/quiz/answer")
def record_answer(body: QuizAnswer):
    return api.record_answer(body.patient_id, body.question_id, body.reply_text)


@app.post("/time")
def log_time(body: TimeEntry):
    return api.log_time(body.chw_id, body.activity, body.patient_id, body.start, body.end)


@app.post("/review/run")
def run_review():
    return api.run_review()


@app.post("/feedback")
def feedback(body: Feedback):
    return api.feedback(body.who, body.target, body.value, body.note)


@app.post("/patients/{patient_id}/goal_override")
def set_goal_override(patient_id: int, body: GoalOverride):
    return api.set_goal_override(patient_id, body.goal_override, body.pin)


@app.get("/errors")
def error_reviews(label: Optional[str] = None):
    return api.error_reviews(label)


@app.post("/errors/detect")
def detect_errors(as_of: Optional[str] = Body(None, embed=True)):
    return api.detect_errors(as_of)


@app.get("/errors/summary")
def error_summary(month: Optional[str] = None):
    return api.error_summary(month)


@app.post("/errors/replay")
def replay_config(body: Replay):
    return api.replay_config(body.changes)


@app.post("/errors/{review_id}/label")
def label_error_review(review_id: int, body: ReviewLabel):
    return api.label_error_review(review_id, body.supervisor_label, body.cause_code, body.pin)


@app.post("/patients/{patient_id}/referral_outcome")
def record_referral_outcome(patient_id: int, body: ReferralOutcome):
    return api.record_referral_outcome(patient_id, body.outcome)


@app.post("/import")
def import_records(body: ImportCsv):
    return api.import_records(body.csv_text)


@app.post("/demo/reset")
def reset_demo(seed: int = Body(1, embed=True)):
    return api.reset_demo(seed)
