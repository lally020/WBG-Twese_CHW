# CHW Compass adaptive SMS agent — fictional demo only

Launch with the normal Mac launcher. Open `http://127.0.0.1:8765/agent-demo`.

1. Choose a **fictional** patient with routine status. Enable the agent and click **Advance agent now**. The simulated phone receives a medication adherence question.
2. Reply **NO**. The agent asks **why** medication was missed. Reply **3** (ran out / cost), and the agent logs a CHW follow-up and stops automated clinical conversation. View `/` and the review queue.
3. For another routine fictional patient, enable and advance, reply **YES**. The agent sends an approved quiz. Reply **A**; the quiz is scored and the conversation returns to idle until the next scheduled follow-up.
4. To demonstrate safeguards, reply **I have chest pain and cannot breathe** during a conversation. The deterministic danger-phrase check overrides the AI classifier, writes an urgent review, and halts further routine conversation.

The status panel exposes `awaiting_adherence`, `awaiting_reason`, `awaiting_quiz`, `awaiting_human`, or `idle`. The agent uses the existing local intent classifier for free-text barrier categories; uncertain and clinical categories require a human. Numeric menu replies are deliberately parsed by conversation state to avoid conflating a reason code with an adherence reply.

**Important limitations:** An incomplete phrase dictionary is not reliable emergency monitoring. No diagnosis, dose changes, treatment, or live SMS are provided. Fictional enrollment is not real patient consent; demo preauthorization is not production governance. This laptop-only app has no real Android integration or telecom connection. For real care, the system needs clinical protocol sign-off, independent validation, explicit consent, local-language testing (Kirundi is not implemented), secure access and delivery acknowledgments.

## What's new
- `agent.py`: persistent per-patient conversation states in the encrypted database, deterministic menu handling, local AI interpretation for free text, safe human handoff.
- `server.py`: replies route through `AGENT.receive_reply` so the agent can retain state and choose the next action.
- `static/agent-demo.html`: shows active state alongside simulated phone conversation.
- `tests/test_agent.py`: tests for missed-dose branching, access barrier, quiz, danger-phrase override, opt-in and no duplicate scheduled messages.

Run `python -m pytest -q` to execute the tests.
