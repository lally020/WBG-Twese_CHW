"""Local-only CHW Compass demo API. Never expose this demo server publicly."""
from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from core import Compass, TRAIN, QUIZ_BANK, TEMPLATES
from agent import FollowupAgent
import threading
import time
from contextlib import asynccontextmanager

BASE=Path(__file__).resolve().parent
SECRET=os.environ.get('COMPASS_PASSPHRASE','')
if len(SECRET)<12:
    raise RuntimeError('Set COMPASS_PASSPHRASE (12+ chars), then run python launch.py')
scheduler_stop = threading.Event()

@asynccontextmanager
async def lifespan(app):
    scheduler_stop.clear()
    EVALS.start()
    worker = threading.Thread(target=agent_background, daemon=True)
    worker.start()
    try:
        yield
    finally:
        EVALS.stop()
        scheduler_stop.set()
        worker.join()

app=FastAPI(lifespan=lifespan,title='CHW Compass • Local demo',docs_url=None,redoc_url=None)
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
C=Compass(Path(os.environ.get('COMPASS_DB_PATH', str(BASE/'private_data'/'demo.sqlite.aes'))),SECRET)
AGENT=FollowupAgent(C)

class VitalsIn(BaseModel):
    sbp:int=Field(ge=50,le=300)
    dbp:int=Field(ge=30,le=200)
    note:str=Field(default='',max_length=500)
class MessageIn(BaseModel):
    transport_id:str|None=Field(default=None,max_length=100)
    body:str=Field(min_length=1,max_length=500)
class ResolveIn(BaseModel):
    reviewer:str=Field(min_length=2,max_length=100)
    decision:str=Field(min_length=2,max_length=500)
class FeedbackIn(BaseModel):
    message_id:int
    corrected_label:str
    reviewer:str
class ScheduleIn(BaseModel):
    patient_id:int
    cadence:str
    categories:list[str]
    days:int=Field(default=30,ge=1,le=90)
class ApproveIn(BaseModel):
    reviewer:str=Field(min_length=2,max_length=100)
class QuizIn(BaseModel):
    patient_id:int
    topic:str
    answer:str

@app.exception_handler(ValueError)
async def validation_handler(request,ex):
    return __import__('fastapi').responses.JSONResponse(status_code=400,content={'detail':str(ex)})

@app.get('/')
def index(): return FileResponse(BASE/'static'/'index.html')

@app.get('/welcome')
def welcome(): return FileResponse(BASE/'static'/'welcome.html')

@app.get('/phone-demo')
def phone_demo():
    return RedirectResponse(
        url='/agent-demo',
        status_code=302
    )

@app.get('/api/health')
def health(): return {'online':True,'offline_inference':True,'demo':True,'actual_sms':False}

@app.get('/api/summary')
def summary(): return C.dashboard()


@app.get('/api/patients')
def patients():
    return C.all_patients()

# Pagination must appear BEFORE /{pid}
@app.get('/api/patients/page')
def patient_page(
    limit: int = 50,
    offset: int = 0
):
    return C.patient_page(
        limit=limit,
        offset=offset
    )

@app.get('/api/patients/{pid}')
def patient(pid: int):
    return C.patient(pid)

@app.post('/api/patients/{pid}/vitals')
def vitals(pid: int, body: VitalsIn):
    return C.add_vitals(
        pid, body.sbp, body.dbp, body.note
    )

@app.post('/api/patients/{pid}/sms')
def sms(pid: int, body: MessageIn):
    return AGENT.receive_reply(pid, body.body, body.transport_id)


@app.get('/api/reviews')
def reviews():return C.reviews()

@app.post('/api/reviews/{rid}/resolve')
def resolve(rid:int,body:ResolveIn):C.resolve_review(rid,body.reviewer,body.decision);return {'ok':True}

@app.post('/api/feedback')
def feedback(body:FeedbackIn):
    C.add_feedback(body.message_id,body.corrected_label,body.reviewer)
    EVALS.update_intent_candidate(C.ai.candidate())
    return {'ok':True,'candidate_evaluated':True,'deployed':False}

@app.get('/api/evaluation')
def evaluation():
    candidate=C.ai.candidate()
    return {'current':C.ai.metrics(),'candidate':candidate['after'],
            'verified_feedback':candidate['feedback_count'],
            'candidate_not_deployed':True,
            'development_only':'Synthetic and small fixed test set. Not an estimate of clinical reliability.'}

@app.post('/api/model/compare')
def model_compare():
    comparison=C.ai.candidate()
    EVALS.update_intent_candidate(comparison)
    return {k:v for k,v in comparison.items() if k!='model'}

@app.post('/api/model/promote')
def model_promote(body:ApproveIn):
    # Offline NLP classifier only. Explicit human approval and no degradation allowed.
    candidate=C.ai.candidate()
    if candidate['feedback_count']<1:raise ValueError('Requires at least one verified correction')
    if candidate['after']['macro_f1']<candidate['before']['macro_f1']:
        raise ValueError('Candidate held-out macro-F1 declined; cannot deploy')
    C.ai.promote(candidate['model'])
    C.audit('model_promoted',f'Classifier version {C.ai.version} approved by {body.reviewer}; testing is synthetic')
    return {'version':C.ai.version,'comparison':{k:v for k,v in candidate.items() if k!='model'},
            'warning':'This is an educational prototype. Production requires independent validation and governance.'}

@app.get('/api/outbox')
def outbox():return C.outbox()

@app.post('/api/outbox/schedule')
def schedule(body:ScheduleIn):return {'queued_for_review':C.schedule(body.patient_id,body.cadence,body.categories,body.days)}

@app.post('/api/outbox/{msg_id}/approve')
def approve(msg_id:int,body:ApproveIn):C.approve_outbox(msg_id,body.reviewer);return {'ok':True}

@app.post('/api/outbox/{msg_id}/simulate')
def deliver(msg_id:int):C.simulate_delivery(msg_id);return {'ok':True,'actual_sms_sent':False}

@app.get('/api/quiz/{pid}/{topic}')
def quiz_question(pid:int,topic:str):return C.quiz_question(pid,topic)

@app.post('/api/quiz/answer')
def quiz_answer(body:QuizIn):return C.quiz_answer(body.patient_id,body.topic,body.answer)

@app.get('/api/meta')
def meta():return {'categories':list(TEMPLATES),'labels':list(TRAIN),'quiz_topics':list(QUIZ_BANK),
                            'languages':['English','Kiswahili'],'sms_network_connected':False,
                            'warning':'Research prototype with entirely synthetic clinical data. Never use for clinical decisions.'}

# Agent is deliberately DISABLED by default for all patients. Only explicitly
# enrolled fictional records receive autonomous, simulated SMS.
class AgentEnrollIn(BaseModel):
    cadence:str='daily'

@app.post('/api/agent/enroll/{pid}')
def agent_enroll(pid: int, body: AgentEnrollIn):
    result = AGENT.enroll(pid, body.cadence)

    # Trigger the first agent action immediately on enrollment.
    # The background scheduler handles subsequent due messages.
    result["initial_actions"] = AGENT.tick()

    return result

@app.post('/api/agent/stop/{pid}')
def agent_stop(pid:int):return AGENT.stop(pid)

@app.post('/api/agent/tick')
def agent_tick():return {'actions':AGENT.tick(),'simulated_only':True}

@app.get('/api/agent/model')
def agent_model_status():return AGENT.conversation_model.status()

@app.get('/api/agent/status')
def agent_status():return AGENT.status()

@app.get('/agent-demo')
def agent_demo():return FileResponse(BASE/'static'/'agent-demo.html')

def agent_background():
    while not scheduler_stop.is_set():
        try: AGENT.tick()
        except Exception as ex: print('Agent scheduler failed (demo only):',type(ex).__name__)
        scheduler_stop.wait(30)


# Evaluation demo uses isolated fictional data; keep this server on localhost.
from eval_dashboard import install_eval_routes
EVALS = install_eval_routes(app, BASE)
