from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator
from sms_bridge import Bridge,Config,create_app
from store import EncryptedStore
from test_agent import setup

@pytest.fixture
def bridge(tmp_path):
    sent=[]
    def send(**kw):
        sent.append(kw)
        return SimpleNamespace(sid='SM'+'b'*32)
    b=Bridge(Config('AC'+'a'*32,'test-token','+15550000001','https://example.test/sms',{'+15550000002':1},True),
        EncryptedStore(tmp_path/'bridge.aes','test-password-123'),send,lambda r:'Thanks for telling us.')
    b.sent=sent
    return b

def payload(b,body='Hello',n='1'):
    return {'AccountSid':b.config.account,'To':b.config.number,'From':'+15550000002','MessageSid':'SM'+n*32,'Body':body,'NumMedia':'0'}

def test_signature_allowlist_and_fast_ack(bridge):
    b=bridge;c=TestClient(create_app(b));p=payload(b)
    assert c.post('/sms',data=p).status_code==403
    def post(p):
        sig=RequestValidator(b.config.token).compute_signature(b.config.webhook,p)
        return c.post('/sms',data=p,headers={'X-Twilio-Signature':sig})
    assert post(p).status_code==200
    assert not b.sent
    assert post(p).status_code==200
    assert len(b.db.all('SELECT * FROM queue'))==1
    p['From']='+15550000003'
    assert post(p).status_code==403
    assert c.get('/').status_code==404
    b.step();b.step()
    assert len(b.sent)==1
    assert b.sent[0]['body'].startswith('Compass DEMO:')

def test_draft_and_opt_out(bridge):
    b=bridge;b.config.live=False
    b.accept(payload(b));b.step()
    assert b.db.one('SELECT state FROM queue')['state']=='draft'
    b.config.live=True
    b.accept(payload(b,'Hi','2'));b.accept(payload(b,'STOP','3'));b.step()
    assert not b.sent
    b.accept(payload(b,'Hello again','4'));b.step();assert not b.sent
    b.accept(payload(b,'START','5'));b.accept(payload(b,'Hi','6'));b.step()
    assert len(b.sent)==1

def test_ambiguous_send_never_retried(bridge):
    b=bridge
    def fail(**kw):raise TimeoutError()
    b.send=fail;b.accept(payload(b));b.step();b.step()
    assert b.db.one('SELECT state FROM queue')['state']=='send_unknown'

def test_processing_retry_same_identity(bridge):
    b=bridge;seen=[]
    def process(row):
        seen.append(row['sid'])
        if len(seen)==1:raise TimeoutError()
        return 'Thanks'
    b.process=process;b.accept(payload(b));b.step();b.step()
    assert len(set(seen))==1 and len(b.sent)==1

def test_restart_and_unsupported(bridge):
    b=bridge;b.accept(payload(b,'x'*501));assert not b.step()
    b.accept(payload(b,'Hello','2'))
    b.db.execute("UPDATE queue SET state='sending' WHERE state='pending'")
    restart=Bridge(b.config,b.db,b.send,b.process)
    assert not restart.step()
    assert b.db.one("SELECT * FROM queue WHERE state='send_unknown'")

@pytest.mark.parametrize('body',['Hello','My knee hurts','RAN OUT'])
def test_main_app_receipt_is_idempotent(tmp_path,body):
    c,a,pid=setup(tmp_path)
    r=a.receive_reply(pid,body,'twilio:unique')
    count=c.db.one('SELECT COUNT(*) AS n FROM messages')['n']
    assert a.receive_reply(pid,body,'twilio:unique')==r
    assert c.db.one('SELECT COUNT(*) AS n FROM messages')['n']==count
    with pytest.raises(ValueError):a.receive_reply(pid,'Changed','twilio:unique')


def test_changed_allowlist_suppresses_old_queue(bridge):
    b=bridge;b.accept(payload(b))
    b.config.patients={'+15550000003':2}
    b.step()
    assert not b.sent
    assert b.db.one('SELECT state FROM queue')['state']=='suppressed'
