"""Run a real local model smoke test, separate from mocked integration tests."""
import sys
from local_llm import LocalConversation
model=LocalConversation()
if not model.model:
 print('Set COMPASS_LOCAL_MODEL first, for example gemma3:4b.');sys.exit(1)
result=model.interpret('The last bottle is empty and I cannot get another until next week.',
 'awaiting_reason','English',[])
print('Model:',model.model)
print('Status:',model.last)
if not result:
 print('No usable local-model response. Open Ollama and verify the model is downloaded.');sys.exit(1)
print('Intent:',result['intent'])
print('Reply:',result['reply'])
print('Human review:',result['needs_review'])
if result['intent']!='access':
 print('Smoke test needs inspection: expected an access barrier.');sys.exit(2)
print('Real inference smoke test passed. This is not clinical validation.')
