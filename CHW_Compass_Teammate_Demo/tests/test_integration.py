import os
import sys
import importlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_agent import setup

@pytest.mark.parametrize('body,action',[
 ('I took my pills this morning','asked_quiz'),
 ('I forgot again','asked_reason'),
 ('I ran out of medicine','human_handoff'),
 ('The medicine makes me dizzy','human_handoff'),
 ('Can someone visit me tomorrow?','human_handoff'),
 ("My chest hurts and I can't breathe",'human_handoff'),
 ("I don't understand your question",'clarified_question'),
 ('xyz 123 ???','human_handoff'),
 ('I took my medicine but did not take all my pills','asked_reason'),
])
def test_visible_free_text(tmp_path,body,action):
 c,a,pid=setup(tmp_path)
 r=a.receive_reply(pid,body)
 assert r['agent']['action']==action
 assert r['reply_persisted']
 assert c.db.one('SELECT body FROM messages WHERE id=?',(r['message_id'],))['body']==body
 assert c.patient(pid)['messages'][0]['direction']=='outbound'
 assert c.patient(pid)['messages'][0]['body']==r['patient_auto_ack']
 if "can't breathe" in body:assert r['danger_flag']


def test_pending_human_and_unenrolled(tmp_path):
 c,a,pid=setup(tmp_path)
 a.receive_reply(pid,'The medicine makes me dizzy')
 assert a.receive_reply(pid,'hello')['reply_persisted']
 a.stop(pid)
 assert a.receive_reply(pid,'I need help')['reply_persisted']
 assert not a._active(pid)


def test_api_workflow(tmp_path,monkeypatch):
 monkeypatch.setenv('COMPASS_PASSPHRASE','integration-test-password')
 monkeypatch.setenv('COMPASS_DB_PATH',str(tmp_path/'api.aes'))
 sys.modules.pop('server',None)
 server=importlib.import_module('server')
 from fastapi.testclient import TestClient
 with TestClient(server.app) as client:
  page=client.get('/api/patients/page?limit=10').json()
  assert len(page['patients'])==10
  pid=next(p['id'] for p in server.C.all_patients() if p['assessment']['priority']=='routine')
  assert client.post(f'/api/agent/enroll/{pid}',json={}).status_code==200
  for text in ('I forgot again','I ran out of medicine','hello'):
   r=client.post(f'/api/patients/{pid}/sms',json={'body':text})
   assert r.status_code==200 and r.json()['reply_persisted']
  assert any(r['patient_id']==pid for r in client.get('/api/reviews').json())
  assert client.get('/api/summary').status_code==200
  for url in ('/','/agent-demo','/static/app.js'):
   assert client.get(url).status_code==200
  assert client.post(f'/api/patients/{pid}/sms',json={'body':'   '}).status_code==400
 from core import Compass
 from agent import FollowupAgent
 again=Compass(tmp_path/'api.aes','integration-test-password')
 assert FollowupAgent(again)._state(pid)['state']=='awaiting_human'
 assert again.patient(pid)['messages'][0]['direction']=='outbound'


def test_tick_bounded_concurrent(tmp_path):
 c,a,pid=setup(tmp_path)
 ids=[p['id'] for p in c.all_patients() if p['assessment']['priority']=='routine' and p['id']!=pid]
 for i in ids:a.enroll(i)
 with ThreadPoolExecutor(2) as pool:
  outputs=list(pool.map(lambda _:a.tick(limit=1),range(2)))
 assert all(len(x)<=1 for x in outputs)
 sent=[x[0]['patient_id'] for x in outputs if x]
 assert len(sent)==len(set(sent))


def test_reply_rolls_back(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 count=c.db.one('SELECT count(*) n FROM messages')['n']
 original=a._send
 def fail(*args):
  original(*args)
  raise RuntimeError('injected failure')
 monkeypatch.setattr(a,'_send',fail)
 with pytest.raises(RuntimeError):a.receive_reply(pid,'NO')
 assert c.db.one('SELECT count(*) n FROM messages')['n']==count
 assert a._state(pid)['state']=='awaiting_adherence'
