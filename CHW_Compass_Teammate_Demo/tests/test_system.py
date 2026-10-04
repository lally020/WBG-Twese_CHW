from pathlib import Path
import pytest
from core import Compass

@pytest.fixture
def system(tmp_path):
    return Compass(tmp_path/'data.aes','test-passphrase-do-not-reuse',seed=True)

def test_seed_and_assessment(system):
    p=system.all_patients()
    assert len(p)==24
    assert p[0]['assessment']['priority']=='urgent'
    assert system.assess(2)['priority']=='urgent'
    assert any('Severely elevated' in x for x in system.assess(2)['flags'])
    assert system.assess(11)['priority']=='urgent'

def test_predict_and_sms_handoff(system):
    out=system.receive_sms(3,'My chest hurts and I cannot breathe')
    assert out['danger_flag']
    assert any(x['patient_id']==3 and x['kind']=='symptoms' and x['urgency']=='urgent' for x in system.reviews())
    assert out['patient_auto_ack'].startswith('Thank you')
    out2=system.receive_sms(3,'YES')
    assert out2['message_id']>0
    assert system.db.one('SELECT taken FROM adherence WHERE patient_id=3 ORDER BY id DESC')['taken']==1

def test_vitals_z_and_rule_divergence(system):
    stat=system.assess(1)
    assert stat['priority'] in ('followup','urgent')
    new=system.add_vitals(1,198,125,'Fictional severe reading')
    assert new['priority']=='urgent'

def test_queue_approval_before_send(system):
    n=system.schedule(3,'weekly',['medication','diet'],days=15)
    assert n==6
    msg=system.db.one('SELECT * FROM outbox ORDER BY id ASC LIMIT 1')
    with pytest.raises(ValueError):system.simulate_delivery(msg['id'])
    system.approve_outbox(msg['id'],'Test CHW')
    system.simulate_delivery(msg['id'])
    assert system.db.one('SELECT status FROM outbox WHERE id=?',(msg['id'],))['status']=='simulated_delivered'

def test_quiz_and_feedback(system):
    q=system.quiz_question(3,'adherence')
    assert q['language']=='English'
    assert system.quiz_answer(3,'adherence','A')['correct']
    msg=system.receive_sms(3,'My medication costs too much')
    system.add_feedback(msg['message_id'],'cost','Test Reviewer')
    cand=system.ai.candidate()
    assert cand['feedback_count']==1
    assert 0<=cand['after']['accuracy']<=1

def test_encryption_at_rest(system):
    raw=system.db.path.read_bytes()
    assert raw.startswith(b'CHWCOMPASS1')
    assert b'Amina M.' not in raw
    from store import EncryptedStore
    with pytest.raises(ValueError):EncryptedStore(system.db.path,'wrong-password-but-long-enough')
    again=EncryptedStore(system.db.path,'test-passphrase-do-not-reuse')
    assert again.one('SELECT COUNT(*) AS n FROM patients')['n']==24

def test_rules_do_not_require_or_change_ai_confidence(system):
    assert system.assess(2)['priority']=='urgent'
    system.ai.version=300
    assert system.assess(2)['priority']=='urgent'

def test_patient_quiz_sms_answer(system):
    n=system.schedule(3,'weekly',['quiz'],days=1)
    assert n==1
    outbound=system.db.one("SELECT id FROM outbox WHERE category='quiz' ORDER BY id DESC LIMIT 1")
    system.approve_outbox(outbound['id'],'Test CHW')
    system.simulate_delivery(outbound['id'])
    result=system.receive_sms(3,'A')
    assert result['label']=='quiz_answer'
    assert result['quiz_correct'] is True
    assert system.db.one('SELECT correct FROM quiz WHERE patient_id=3 ORDER BY id DESC LIMIT 1')['correct']==1
