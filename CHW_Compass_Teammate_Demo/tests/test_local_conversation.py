import json
import pytest
from local_llm import LocalConversation
from test_agent import setup

class Response:
 def __init__(self,output):self.output=output
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,n):return json.dumps({'message':{'content':json.dumps(self.output)}}).encode()

@pytest.mark.parametrize('output',[
 {'intent':'other','reply':'What would you like help with?','needs_review':True,'urgent':False},
 {'intent':'access','reply':'Is it a refill problem?','needs_review':True,'urgent':False},
])
def test_local_protocol(monkeypatch,output):
 monkeypatch.setenv('COMPASS_LOCAL_MODEL','test-model')
 seen=[]
 class Opener:
  def open(self,request,timeout):
   seen.append(json.loads(request.data))
   assert request.full_url=='http://127.0.0.1:11434/api/chat'
   return Response(output)
 monkeypatch.setattr('urllib.request.build_opener',lambda *a:Opener())
 model=LocalConversation()
 assert model.interpret('unfamiliar wording','idle','English',[])==output
 assert model.last=='responding'
 assert seen[0]['stream'] is False and isinstance(seen[0]['format'],dict)

@pytest.mark.parametrize('output',[
 {'intent':'other','reply':'hello','needs_review':'false','urgent':False},
 {'intent':'unsupported','reply':'hello','needs_review':False,'urgent':False},
 {'intent':'other','reply':'','needs_review':False,'urgent':False},
])
def test_invalid_output_falls_back(monkeypatch,output):
 monkeypatch.setenv('COMPASS_LOCAL_MODEL','test-model')
 class Opener:
  def open(self,*a,**k):return Response(output)
 monkeypatch.setattr('urllib.request.build_opener',lambda *a:Opener())
 model=LocalConversation()
 assert model.interpret('hello','idle','English',[]) is None
 assert model.last.startswith('unavailable_or_invalid:')

@pytest.mark.parametrize('intent,body,action',[
 ('access','The last bottle is empty','human_handoff'),
 ('side_effect','I feel strange after each dose','human_handoff'),
 ('appointment','Please arrange a check-in next week','human_handoff'),
 ('symptoms','Something is bothering me physically','human_handoff'),
 ('taken','Finished my usual morning dose','human_handoff'),
 ('other','Can you help me with something?','human_handoff'),
])
def test_model_routes_unfamiliar_wording(tmp_path,monkeypatch,intent,body,action):
 c,a,pid=setup(tmp_path)
 response={'intent':intent,'reply':'What would you like help with?','needs_review':True,'urgent':False}
 monkeypatch.setattr(a.conversation_model,'interpret',lambda *args:response)
 before=len(c.patient(pid)['adherence'])
 result=a.receive_reply(pid,body)
 assert result['conversation_engine']=='local_model'
 assert result['agent']['action']==action
 assert result['reply_persisted']
 assert c.patient(pid)['messages'][0]['direction']=='outbound'
 assert len(c.patient(pid)['adherence'])==before


def test_urgent_model_flag(tmp_path,monkeypatch):
 c,a,pid=setup(tmp_path)
 monkeypatch.setattr(a.conversation_model,'interpret',lambda *args:{'intent':'symptoms','reply':'hello','needs_review':True,'urgent':True})
 result=a.receive_reply(pid,'An unfamiliar urgent symptom description')
 assert result['danger_flag']
 assert 'urgent' in result['patient_auto_ack']
 assert c.db.one("SELECT id FROM review WHERE patient_id=? AND urgency='urgent'",(pid,))


def test_food_not_medication_access(tmp_path):
 c,a,pid=setup(tmp_path)
 a.receive_reply(pid,'I ran out of food')
 assert not c.db.one("SELECT id FROM review WHERE patient_id=? AND rationale='Patient reports medication supply or affordability barrier'",(pid,))


def test_forgot_response(tmp_path):
 c,a,pid=setup(tmp_path)
 a.receive_reply(pid,'NO')
 r=a.receive_reply(pid,'1')
 assert 'forgot' in r['patient_auto_ack'] and 'reminder' in r['patient_auto_ack']
