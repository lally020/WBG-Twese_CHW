"""Bounded automatic urgency-model improvement for the fictional demo only."""
from __future__ import annotations
import argparse, hashlib, json, os, signal, tempfile, time
from pathlib import Path
from contextlib import contextmanager
ROOT=Path(__file__).resolve().parent
RELEASE_SHA256='fc4445f76566999c2a747b12625f05ee52dc079000a59348990d5bc21efe965d'

def load_rows(path):return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else 'baseline'
def atomic(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2));os.replace(tmp,path)

@contextmanager
def cycle_lock(registry):
    registry.mkdir(parents=True,exist_ok=True);path=registry/'cycle.lock'
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        try:pid=int(path.read_text());os.kill(pid,0)
        except ProcessLookupError:path.unlink();fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        else:raise RuntimeError('Another improvement or rollback is running')
    os.write(fd,str(os.getpid()).encode());os.close(fd)
    try:yield
    finally:path.unlink(missing_ok=True)

def classification(model,cases):
    from run_evals import binary_metrics
    rows=[dict(id=c['id'],text=c['text'],expected=c['expected_flag'],predicted=model.predict(c['text'],c.get('language','English'))['label']) for c in cases]
    return {'metrics':binary_metrics(rows),'rows':rows}

def workflows(model,cases):
    from core import Compass,now
    from agent import FollowupAgent
    from run_evals import binary_metrics
    rows=[]
    for case in cases:
        with tempfile.TemporaryDirectory(prefix='compass-candidate-') as td:
            c=Compass(Path(td)/'data.aes','fictional-candidate-only-passphrase',seed=False)
            c.db.execute('INSERT INTO patients(id,name,phone,language,condition,created_at) VALUES(?,?,?,?,?,?)',(1,'Synthetic release case','+00000000000',case.get('language','English'),'Hypertension',now()))
            c.db.execute('INSERT INTO vitals(patient_id,recorded_at,sbp,dbp) VALUES(?,?,?,?)',(1,now(),125,78))
            a=FollowupAgent(c);a.urgency_model=model;a.enroll(1);a.tick()
            for i,t in enumerate(case.get('turns',[case]),1):
                started=time.perf_counter();out=a.receive_reply(1,t['text']);elapsed=time.perf_counter()-started
                rows.append({'id':case['id']+f'.{i}','expected':t['expected_flag'],'predicted':'urgent' if out.get('danger_flag') else 'nonurgent',
                    'reply_ok':bool(out.get('reply_persisted') and out.get('patient_auto_ack','').strip()),'elapsed':elapsed,
                    'review':bool(c.db.one("SELECT id FROM review WHERE patient_id=1 AND status='open'")),
                    'urgent_task':bool(c.db.one("SELECT id FROM review WHERE patient_id=1 AND status='open' AND urgency='urgent'")),
                    'reply':out.get('patient_auto_ack'),'engine':out.get('conversation_engine')})
    return {'metrics':binary_metrics(rows),'rows':rows}

def activation_checks(before,after,bw,aw,bc,ac,main_before,main_after,max_seconds=5):
    b,a=before['metrics'],after['metrics'];bm,am=bw['metrics'],aw['metrics']
    no_new_miss=lambda old,new:all(n['predicted']=='urgent' for o,n in zip(old,new) if o['expected']=='urgent' and o['predicted']=='urgent')
    return {
      'urgency_false_negatives_strictly_reduced':a['fn']<b['fn'],
      'urgency_false_positives_not_increased':a['fp']<=b['fp'],
      'urgency_f1_improved':(a['f1'] or 0)>(b['f1'] or 0),
      'no_new_missed_urgent_classifier_cases':no_new_miss(before['rows'],after['rows']),
      'workflow_missed_urgent_not_increased':am['fn']<=bm['fn'],
      'workflow_false_alarms_not_increased':am['fp']<=bm['fp'],
      'no_new_missed_urgent_workflow_cases':no_new_miss(bw['rows'],aw['rows']),
      'all_replies_persisted':all(r['reply_ok'] for r in aw['rows']+ac['rows']),
      'workflow_within_time_budget':all(r['elapsed']<=max_seconds for r in aw['rows']+ac['rows']),
      'conversation_urgent_flags_preserved':no_new_miss(bc['rows'],ac['rows']),
      'conversation_false_alarms_not_increased':ac['metrics']['fp']<=bc['metrics']['fp'],
      'conversation_review_actions_preserved':all((not old['review'] or new['review']) and (not old['urgent_task'] or new['urgent_task']) for old,new in zip(bc['rows'],ac['rows'])),
      'main_policy_checks_pass':main_after==main_before and all(main_after),
    }

def main_checks():
    from run_evals import worker
    cfg={'mode':'rules','llm_evaluator':{'enabled':False}}
    return [worker({'config':cfg,'case':c,'suite':'main_system'})['turns'][0]['predicted']==c['expected_priority'] for c in load_rows(ROOT/'evaluation/patients.jsonl')]

def cycle(base=ROOT):
    from urgency_ai import UrgencyClassifier,URGENT,NONURGENT
    base=Path(base);registry=base/'evaluation/model_registry';active=registry/'active.json';last=registry/'last_cycle.json'
    with cycle_lock(registry):
        start_digest=digest(active)
        incumbent_spec=json.loads(active.read_text()) if active.exists() else UrgencyClassifier.base_spec()
        old=UrgencyClassifier(incumbent_spec,use_registry=False)
        development=load_rows(base/'evaluation/messages.jsonl');release_path=base/'evaluation/release_gate.jsonl'
        key=hashlib.sha256((''.join(digest(base/name) for name in ('auto_improve.py','urgency_ai.py','agent.py','pain_history.py','core.py','evaluation/messages.jsonl','evaluation/release_gate.jsonl'))+start_digest).encode()).hexdigest()
        if last.exists():
            previous=json.loads(last.read_text())
            if previous.get('input_key')==key:
                print('Unchanged inputs; retaining previous decision.',flush=True);return previous
        print('1/5 Evaluate current classifier on labeled development messages',flush=True)
        before_dev=classification(old,development)
        failed_ids={r['id'] for r in before_dev['rows'] if r['expected']!=r['predicted']}
        extra={r['text']:r['label'] for r in incumbent_spec.get('extra',[])};learned=[]
        for c in development:
            if c['id'] in failed_ids and c['expected_flag'] in ('urgent','nonurgent') and c.get('language','English')=='English' and len(c['text'])>=5:
                extra[c['text']]=c['expected_flag'];learned.append(c['id'])
        if digest(release_path)!=RELEASE_SHA256:raise ValueError('Frozen release dataset changed; create and review a new release protocol explicitly')
        release=load_rows(release_path);normal=lambda s:' '.join(s.lower().split())
        training={normal(t) for t in URGENT+NONURGENT+list(extra)}
        if any(normal(c['text']) in training for c in release):raise ValueError('Training and release-gate overlap detected')
        if not release or len({c['id'] for c in release})!=len(release):raise ValueError('Invalid release dataset')
        print('2/5 Train bounded candidates; select on development data only',flush=True)
        candidates=[]
        for strength in (1.,3.,10.):
            for urgent_threshold in (.50,.55,.60):
                spec={'version':'candidate','C':strength,'urgent_threshold':urgent_threshold,'nonurgent_threshold':.40,
                      'extra':[{'text':t,'label':label} for t,label in sorted(extra.items())]}
                m=UrgencyClassifier(spec,use_registry=False);sc=classification(m,development);v=sc['metrics']
                candidates.append(((-v['fn'],-v['fp'],v['f1'] or 0,-v['abstentions'],-strength),spec,sc))
        admissible=[c for c in candidates if c[2]['metrics']['fp']<=before_dev['metrics']['fp']]
        if not admissible:raise ValueError('No candidate preserved development false positives')
        _,spec,dev=max(admissible,key=lambda c:c[0])
        stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime());spec['version']='synthetic-auto-'+stamp
        candidate=UrgencyClassifier(spec,use_registry=False)
        print('3/5 Compare selected candidate and incumbent on frozen release cases',flush=True)
        before=classification(old,release);after=classification(candidate,release)
        print('4/5 Check full message workflow, conversations and main priorities',flush=True)
        live_conversation_model=os.environ.get('COMPASS_LOCAL_MODEL','').strip()
        os.environ['COMPASS_URGENCY_POLICY']='baseline';os.environ.pop('COMPASS_LOCAL_MODEL',None)
        bw=workflows(old,release);aw=workflows(candidate,release)
        conversations=load_rows(base/'evaluation/conversations.jsonl');bc=workflows(old,conversations);ac=workflows(candidate,conversations)
        mb=main_checks();ma=main_checks()
        for a,b in ((before['rows'],after['rows']),(bw['rows'],aw['rows']),(bc['rows'],ac['rows'])):
            if [r['id'] for r in a]!=[r['id'] for r in b]:raise ValueError('Mismatched comparison cases')
        checks=activation_checks(before,after,bw,aw,bc,ac,mb,ma)
        checks['tested_conversation_engine_matches_live']=not bool(live_conversation_model)
        result={'input_key':key,'created_at':stamp,'development_data_hash':digest(base/'evaluation/messages.jsonl'),
          'release_data_hash':digest(release_path),'candidate_count':len(candidates),'learned_case_ids':learned,
          'before_version':old.version,'candidate_version':spec['version'],'live_conversation_model':live_conversation_model or None,'before':before,'after':after,
          'workflow_before':bw,'workflow_after':aw,'conversations_before':bc,'conversations_after':ac,
          'development_before':before_dev,'development_after':dev,'gates':checks,
          'status':'activated' if all(checks.values()) else 'rejected',
          'scope':'Fictional demo only; synthetic unreviewed labels; rules-mode workflow; not clinical validation.',
          'release_set_use':'Not used in fitting or candidate selection; repeated release checks are not a fresh external validation.'}
        atomic(registry/'candidates'/f'{stamp}.json',spec)
        if digest(active)!=start_digest:raise ValueError('Active model changed during evaluation')
        if result['status']=='activated':atomic(registry/'previous.json',incumbent_spec);atomic(active,spec)
        atomic(registry/'history'/f'{stamp}.json',result);atomic(last,result)
        print('5/5 '+result['status'].upper()+': '+json.dumps(checks),flush=True)
        return result

def rollback(base=ROOT):
    registry=Path(base)/'evaluation/model_registry'
    with cycle_lock(registry):
        p=registry/'previous.json'
        if not p.exists():raise ValueError('No previous model available')
        from urgency_ai import UrgencyClassifier
        spec=json.loads(p.read_text());UrgencyClassifier(spec,use_registry=False)
        atomic(registry/'active.json',spec);atomic(registry/'rollback.json',{'time':time.time(),'restored':spec['version']})
        return {'restored':spec['version']}

if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit(143)))
    ap=argparse.ArgumentParser();ap.add_argument('--rollback',action='store_true');args=ap.parse_args()
    if args.rollback:print(json.dumps(rollback()))
    else:cycle()
