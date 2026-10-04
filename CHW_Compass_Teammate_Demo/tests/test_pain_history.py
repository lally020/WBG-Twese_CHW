import pytest
from test_agent import setup
from pain_history import FIELDS


def test_complete_pain_history(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'uncertain','urgent_score':.5,'version':'test','needs_review':True})
 monkeypatch.setattr(a.conversation_model,'interpret',lambda *args:(_ for _ in ()).throw(AssertionError('structured flow must bypass LLM')))
 r=a.receive_reply(pid,'My knee hurts')
 assert r['agent']['field']=='onset'
 answers=['Started gradually yesterday','Walking makes it worse','Rest helps','It feels dull','It stays in the knee','3','It comes and goes for a few minutes']
 for answer in answers:r=a.receive_reply(pid,answer)
 assert r['agent']['action']=='pain_history_complete'
 pain=c.patient(pid)['pain_history']
 assert set(pain['details'])==set(FIELDS)
 assert pain['details']['region']=='knee'
 assert pain['details']['severity']=='3/10'
 assert pain['status']=='complete'
 review=c.db.one('SELECT * FROM review WHERE id=?',(pain['review_id'],))
 assert 'Walking makes it worse' in review['rationale']
 assert 'Rest helps' in review['rationale']
 assert review['status']=='open'
 assert a._state(pid)['state']=='awaiting_human'
 assert c.patient(pid)['messages'][0]['direction']=='outbound'


def test_skip_known_details_and_invalid_severity(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.2,'version':'test','needs_review':False})
 r=a.receive_reply(pid,'Dull pain in my right knee since yesterday, 3/10, comes and goes')
 assert r['agent']['field']=='provocation'
 a.receive_reply(pid,'Walking')
 a.receive_reply(pid,'Rest')
 r=a.receive_reply(pid,'It does not spread')
 assert r['agent']['action']=='pain_history_complete'


def test_severity_reasks(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.2,'version':'test','needs_review':False})
 a.receive_reply(pid,'Dull pain in my knee since yesterday')
 for text in ('Walking','Rest','It stays in the knee'):a.receive_reply(pid,text)
 r=a.receive_reply(pid,'twenty')
 assert r['agent']['field']=='severity'
 assert 'severity' not in r['pain_history']['details']
 r=a.receive_reply(pid,'4/10')
 assert r['agent']['field']=='timing'


@pytest.mark.parametrize('text',['My chest hurts','I have chest tightness','I have knee pain and cannot breathe'])
def test_urgent_blocks_initial_intake(tmp_path,monkeypatch,text):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.1,'version':'test','needs_review':False})
 r=a.receive_reply(pid,text)
 assert r['danger_flag']
 assert r['agent']['action']=='human_handoff'
 assert not c.patient(pid)['pain_history']
 assert c.patient(pid)['assessment']['priority']=='urgent'


def test_red_flag_interrupts_mid_interview(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.1,'version':'test','needs_review':False})
 a.receive_reply(pid,'My knee hurts')
 r=a.receive_reply(pid,"Now I can't breathe")
 assert r['danger_flag']
 assert r['pain_history']['status']=='interrupted_urgent'
 assert 'When' not in r['patient_auto_ack']
 assert c.dashboard()['urgent']>=1


def test_pain_restart_and_cancel(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.2,'version':'test','needs_review':False})
 a.receive_reply(pid,'My knee hurts')
 from core import Compass
 from agent import FollowupAgent
 again=Compass(tmp_path/'db.aes','test-passphrase-for-demo')
 agent=FollowupAgent(again)
 monkeypatch.setattr(agent.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.2,'version':'test','needs_review':False})
 r=agent.receive_reply(pid,'Started yesterday')
 assert r['agent']['field']=='provocation'
 r=agent.receive_reply(pid,'cancel')
 assert r['agent']['action']=='pain_intake_cancelled'
 assert r['pain_history']['status']=='cancelled'


def test_urgent_vitals_block_intake(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 c.add_vitals(pid,190,125,'synthetic')
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.1,'version':'test','needs_review':False})
 r=a.receive_reply(pid,'My knee hurts')
 assert r['danger_flag']
 assert r['agent']['action']=='human_handoff'
