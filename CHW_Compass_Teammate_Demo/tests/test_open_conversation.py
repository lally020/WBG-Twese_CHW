from test_agent import setup


def test_open_conversation_continues_and_preserves_history(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 seen=[]
 replies=iter([
  {'intent':'forgot','reply':'What tends to get in the way of remembering?','needs_review':False,'urgent':False},
  {'intent':'other','reply':'Would a message after your night shift work better?','needs_review':False,'urgent':False},
 ])
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.2,'version':'test','needs_review':False})
 def interpret(body,state,lang,history):
  seen.append(history)
  return next(replies)
 monkeypatch.setattr(a.conversation_model,'interpret',interpret)
 first=a.receive_reply(pid,'I keep forgetting my medicine')
 second=a.receive_reply(pid,'I work nights, so mornings are difficult')
 assert first['patient_auto_ack']=='What tends to get in the way of remembering?'
 assert second['patient_auto_ack']=='Would a message after your night shift work better?'
 assert a._state(pid)['state']=='conversation_open'
 assert any('get in the way' in m['body'] for m in seen[1])
 assert not c.db.one("SELECT id FROM outbox WHERE patient_id=? AND category='quiz'",(pid,))


def test_classifier_urgent_updates_dashboard(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'urgent','urgent_score':.9,'version':'test','needs_review':True})
 monkeypatch.setattr(a.conversation_model,'interpret',lambda *args:(_ for _ in ()).throw(AssertionError('must bypass model')))
 result=a.receive_reply(pid,'An unfamiliar high risk description')
 assert result['danger_flag']
 assert c.patient(pid)['assessment']['priority']=='urgent'
 assert c.patient(pid)['sms_urgency']['label']=='urgent'
 urgent=c.dashboard()['urgent']
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.1,'version':'test','needs_review':False})
 monkeypatch.setattr(a.conversation_model,'interpret',lambda *args:{'intent':'greeting','reply':'Hello, how can I help?','needs_review':False,'urgent':False})
 next_reply=a.receive_reply(pid,'Hello, I am back')
 assert next_reply['danger_flag']
 assert 'urgent' in next_reply['patient_auto_ack']
 assert c.patient(pid)['assessment']['priority']=='urgent'
 assert c.dashboard()['urgent']==urgent


def test_generated_treatment_withheld(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.urgency_model,'predict',lambda *args:{'label':'nonurgent','urgent_score':.2,'version':'test','needs_review':False})
 monkeypatch.setattr(a.conversation_model,'interpret',lambda *args:{'intent':'other','reply':'Take 20 mg of the medicine.','needs_review':False,'urgent':False})
 r=a.receive_reply(pid,'What should I do next?')
 assert '20 mg' not in r['patient_auto_ack']
 assert r['response_source']=='guarded_fallback'
 assert a._state(pid)['state']=='awaiting_human'


def test_unknown_language_abstains():
 from urgency_ai import UrgencyClassifier
 m=UrgencyClassifier()
 assert m.predict('sina pesa ya dawa','Kiswahili')['label']=='uncertain'
 assert m.predict('???')['label']=='uncertain'


def test_model_failure_still_classifies_and_replies(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.conversation_model,'interpret',lambda *args:None)
 result=a.receive_reply(pid,'Could you explain what you meant?')
 assert result['reply_persisted']
 assert result['conversation_engine']=='rules_fallback'
 assert result['sms_urgency']['version']=='synthetic-urgency-v1'
 assert c.patient(pid)['sms_urgency']['message_id']==result['message_id']
