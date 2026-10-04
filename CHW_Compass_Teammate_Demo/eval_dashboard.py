"""Local evaluation UI: fixed subprocess command, isolated data, bounded fictional urgency-model updates."""
from pathlib import Path
import hashlib, json, os, subprocess, sys, threading, time
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

class ReviewIn(BaseModel):
    suite: str
    case_id: str
    expected: str
    reviewer: str = Field(min_length=2,max_length=100)
    rationale: str = Field(min_length=5,max_length=1000)

class WatchIn(BaseModel):
    enabled: bool

class EvalService:
    def __init__(self,base):
        self.base=Path(base);self.lock=threading.RLock();self.stop_event=threading.Event()
        self.running=False;self.watch=True;self.auto=os.environ.get('COMPASS_AUTO_IMPROVE','1')=='1';self.reason='Not run in this session';self.error=None
        self.process=None;self.thread=None;self.completed=None;self.generation=0;self.pending=False
        self.intent_candidate=None
        settings=self.base/'evaluation/automation_settings.json'
        if settings.exists() and 'COMPASS_AUTO_IMPROVE' not in os.environ:
            try:self.auto=bool(json.loads(settings.read_text()).get('auto',True))
            except ValueError:self.auto=False
        self.fingerprint=self.signature();self.last_change=0
    def signature(self):
        paths=list(self.base.glob('*.py'))+[self.base/'evals.yaml']+[self.base/'evaluation'/name for name in ('messages.jsonl','patients.jsonl','conversations.jsonl','release_gate.jsonl')]
        h=hashlib.sha256()
        for p in sorted(paths):
            if p.exists():h.update(str(p.relative_to(self.base)).encode());h.update(p.read_bytes())
        return h.hexdigest()
    def start(self):
        self.stop_event.clear()
        self.thread=threading.Thread(target=self.watch_loop,daemon=True);self.thread.start()
        if self.auto and (self.base/'auto_improve.py').exists():self.start_run('Automatic startup evaluation and improvement')
    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.process and self.process.poll() is None:self.process.terminate()
        if self.thread:self.thread.join(timeout=2)
    def watch_loop(self):
        while not self.stop_event.wait(2):
            try:
                if not self.watch:continue
                sig=self.signature()
                if sig!=self.fingerprint:
                    self.fingerprint=sig;self.last_change=time.monotonic();self.pending=True
                if self.pending and time.monotonic()-self.last_change>=3:
                    if self.start_run('Automatic check after saved changes'):self.pending=False
            except Exception as e:self.error=type(e).__name__
    def start_run(self,reason='Manual evaluation'):
        with self.lock:
            if self.running:return False
            self.running=True;self.reason=reason;self.error=None
            self.generation+=1
        threading.Thread(target=self.run,daemon=True).start();return True
    def run(self):
        try:
            out=self.base/'evaluation/runs';out.mkdir(parents=True,exist_ok=True)
            log=out/'live.log'
            args=[sys.executable,str(self.base/'run_evals.py'),'--baseline',str(self.base/'evaluation/baseline_rules/report.json')]
            env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
            with log.open('w') as f:
                with self.lock:
                    if self.stop_event.is_set():return
                    self.process=subprocess.Popen(args,cwd=self.base,env=env,stdout=f,stderr=subprocess.STDOUT)
                rc=self.process.wait()
            if rc not in (0,1):self.error=f'Evaluator exited with code {rc}; check progress log.'
            elif rc==1 and not any('Report:' in line for line in log.read_text().splitlines()):self.error='Evaluation did not complete; check progress log.'
            else:
                self.completed=time.time()
                if self.auto and not self.stop_event.is_set() and (self.base/'auto_improve.py').exists():
                    self.reason='Training and checking an urgency candidate'
                    active_path=self.base/'evaluation/model_registry/active.json'
                    before_hash=hashlib.sha256(active_path.read_bytes()).hexdigest() if active_path.exists() else 'baseline'
                    with log.open('a') as f:
                        self.process=subprocess.Popen([sys.executable,str(self.base/'auto_improve.py')],cwd=self.base,env=env,stdout=f,stderr=subprocess.STDOUT)
                        code=self.process.wait()
                    if code:self.error='Automatic improvement stopped. Check the active model version and progress log.'
                    else:
                        record=self.base/'evaluation/model_registry/last_cycle.json'
                        after_hash=hashlib.sha256(active_path.read_bytes()).hexdigest() if active_path.exists() else 'baseline'
                        if after_hash!=before_hash and not self.stop_event.is_set():
                            self.reason='Rerunning development evaluation with the activated model'
                            with log.open('a') as f:
                                self.process=subprocess.Popen(args,cwd=self.base,env=env,stdout=f,stderr=subprocess.STDOUT)
                                code=self.process.wait()
                            if code not in (0,1):self.error='Post-update evaluation did not complete.'
        except Exception as e:self.error=type(e).__name__+': '+str(e)
        finally:
            with self.lock:self.running=False;self.process=None
    def reports(self):
        found=[]
        base=self.base/'evaluation/baseline_rules/report.json'
        paths=([base] if base.exists() else [])+sorted((self.base/'evaluation/runs').glob('*/report.json'))
        for p in paths:
            try:
                r=json.loads(p.read_text())
                if 'passed' in r:found.append((p,r))
            except (ValueError,OSError):continue
        return found
    def status(self):
        reports=self.reports();log=self.base/'evaluation/runs/live.log'
        reviews=self.base/'evaluation/reviewed_feedback.jsonl'
        registry=self.base/'evaluation/model_registry'
        cycle_path=registry/'last_cycle.json'
        improvement=json.loads(cycle_path.read_text()) if cycle_path.exists() else None
        model=registry/'active.json'
        active_version=json.loads(model.read_text())['version'] if model.exists() else 'synthetic-urgency-v1'
        snapshot=registry/'example_cycle.json'
        example=json.loads(snapshot.read_text()) if snapshot.exists() else None
        return {'running':self.running,'watch':self.watch,'reason':self.reason,'error':self.error,
          'progress':log.read_text()[-1600:] if log.exists() else '',
          'report':reports[-1][1] if reports else None,'report_count':len(reports),
          'review_count':len(reviews.read_text().splitlines()) if reviews.exists() else 0,
          'intent_candidate':self.intent_candidate,'auto':self.auto,'improvement':improvement,'example_improvement':example,'active_urgency_version':active_version,'can_rollback':(registry/'previous.json').exists(),
          'model_report_stale':bool(reports and reports[-1][1].get('urgency_policy_hash','baseline')!=(hashlib.sha256(model.read_bytes()).hexdigest() if model.exists() else 'baseline')),
          'source_changed':bool(reports and {str(p.relative_to(self.base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.base.glob('*.py')} != reports[-1][1].get('source_hashes',{}))}
    def review(self,body):
        # Resolve only known synthetic test cases; never accept arbitrary patient text.
        files={'classifier':'messages.jsonl','messages':'messages.jsonl','main_system':'patients.jsonl','conversations':'conversations.jsonl'}
        if body.suite not in files:raise ValueError('Unknown suite')
        cases=[json.loads(line) for line in (self.base/'evaluation'/files[body.suite]).read_text().splitlines() if line.strip()]
        case=next((c for c in cases if c['id']==body.case_id),None)
        if not case:raise ValueError('Unknown case')
        allowed=['routine','followup','urgent'] if body.suite=='main_system' else ['nonurgent','uncertain','urgent']
        if body.expected not in allowed:raise ValueError('Invalid label')
        if body.suite=='conversations':raise ValueError('Review conversation turns in the dataset with a clinician; this form supports single-message cases only.')
        entry={'created_at':time.time(),'suite':body.suite,'case_id':body.case_id,'expected':body.expected,'reviewer':body.reviewer,'rationale':body.rationale,'case':case,'use':'development_only_not_test_or_training_automatically'}
        with self.lock:
            with (self.base/'evaluation/reviewed_feedback.jsonl').open('a') as f:f.write(json.dumps(entry)+'\n')
        return {'saved':True,'model_changed':False,'test_labels_changed':False}
    def update_intent_candidate(self,c):
        self.intent_candidate={k:v for k,v in c.items() if k!='model'}


def install_eval_routes(app,base):
    svc=EvalService(base)
    @app.get('/eval-demo')
    def page():return FileResponse(Path(base)/'static/eval-demo.html')
    @app.get('/api/evals/status')
    def status():return svc.status()
    @app.post('/api/evals/run')
    def run():
        if not svc.start_run():raise HTTPException(409,'An evaluation is already running')
        return {'started':True}
    @app.post('/api/evals/watch')
    def watch(body:WatchIn):
        svc.watch=body.enabled;svc.fingerprint=svc.signature();svc.pending=False
        return {'enabled':svc.watch}
    @app.post('/api/evals/auto')
    def automatic(body:WatchIn):
        if svc.running:raise HTTPException(409,'Wait for the current cycle to finish before changing automatic improvement')
        from auto_improve import atomic
        svc.auto=body.enabled
        atomic(Path(base)/'evaluation/automation_settings.json',{'auto':svc.auto})
        return {'enabled':svc.auto}
    @app.post('/api/evals/rollback')
    def restore():
        if svc.running:raise HTTPException(409,'Wait for the current cycle to finish before rollback')
        from auto_improve import rollback,atomic
        result=rollback(base);svc.auto=False
        atomic(Path(base)/'evaluation/automation_settings.json',{'auto':False})
        return result
    @app.post('/api/evals/review')
    def review(body:ReviewIn):return svc.review(body)
    return svc
