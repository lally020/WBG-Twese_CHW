from core import Compass
from agent import FollowupAgent


def setup(tmp_path):
    c=Compass(tmp_path/'db.aes','test-passphrase-for-demo')
    a=FollowupAgent(c)
    pid=next(p['id'] for p in c.all_patients() if p['assessment']['priority']=='routine')
    a.enroll(pid,'daily')
    assert a.tick()[0]['action']=='sent_simulated'
    return c,a,pid


def test_agent_autonomy(tmp_path):
    c=Compass(tmp_path/'db.aes','test-passphrase-for-demo')
    a=FollowupAgent(c)
    assert a.tick()==[]
    pid=next(p['id'] for p in c.all_patients() if p['assessment']['priority']=='routine')
    a.enroll(pid)
    assert a.tick()[0]['action']=='sent_simulated'
    assert c.db.one("SELECT COUNT(*) AS n FROM messages WHERE patient_id=? AND direction='outbound' AND status='simulated_only'",(pid,))['n']==1
    assert a.tick()==[]
    a.stop(pid)
    assert a.tick()==[]
    assert a._state(pid)['state']=='idle'


def test_adaptive_access_barrier(tmp_path):
    c,a,pid=setup(tmp_path)
    reply=a.receive_reply(pid,'NO')
    assert reply['agent']['action']=='asked_reason'
    assert a._state(pid)['state']=='awaiting_reason'
    assert 'What made it difficult?' in c.patient(pid)['messages'][0]['body']
    before=len(c.patient(pid)['adherence'])
    reply=a.receive_reply(pid,'3')
    assert reply['agent']['action']=='human_handoff'
    assert a._state(pid)['state']=='awaiting_human'
    assert len(c.patient(pid)['adherence'])==before  # menu option 3 is NOT interpreted as adherence
    assert c.db.one("SELECT COUNT(*) AS n FROM review WHERE patient_id=? AND kind='agent_followup'",(pid,))['n']>=1


def test_yes_then_quiz(tmp_path):
    c,a,pid=setup(tmp_path)
    assert a.receive_reply(pid,'YES')['agent']['action']=='asked_quiz'
    assert a._state(pid)['state']=='awaiting_quiz'
    result=a.receive_reply(pid,'A')
    assert result['agent']['action']=='quiz_complete'
    assert result.get('quiz_correct') is True or result.get('quiz_correct') == 1
    assert a._state(pid)['state']=='idle'


def test_no_then_forgot(tmp_path):
    c,a,pid=setup(tmp_path)
    a.receive_reply(pid,'NO')
    before=len(c.patient(pid)['adherence'])
    result=a.receive_reply(pid,'1')
    assert result['agent']['action']=='recorded_followup'
    assert len(c.patient(pid)['adherence'])==before
    assert a._state(pid)['state']=='idle'


def test_danger_overrides_everything(tmp_path):
    c,a,pid=setup(tmp_path)
    a.receive_reply(pid,'NO')
    result=a.receive_reply(pid,'I have chest pain and cannot breathe')
    assert result['danger_flag']
    assert a._state(pid)['state']=='awaiting_human'
    assert c.db.one("SELECT COUNT(*) AS n FROM review WHERE patient_id=? AND urgency='urgent'",(pid,))['n']>0


def test_urgent_not_sent(tmp_path):
    c=Compass(tmp_path/'db.aes','test-passphrase-for-demo')
    a=FollowupAgent(c)
    pid=2
    assert c.assess(pid)['priority']=='urgent'
    a.enroll(pid)
    assert a.tick()[0]['action']=='human_handoff'
    assert not c.db.one('SELECT id FROM outbox WHERE patient_id=?',(pid,))
