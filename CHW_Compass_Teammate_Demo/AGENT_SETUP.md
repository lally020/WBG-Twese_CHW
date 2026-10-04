# CHW Compass agent demo

This is simulation-only software using fictional patients. Do not use for clinical care or real SMS outreach.

## Launch

Start as usual with `bash run_mac.command` (install packages on first run). Then open:

http://127.0.0.1:8765/agent-demo

1. Select fictional Routine patient.
2. Click **Enable fictional consent + agent**.
3. Click **Advance agent now** for instant demonstration. The background scheduler also checks every 30 seconds while the process runs.
4. See automatic medication and quiz messages in phone interface without manual per-message send approval.
5. Reply `NO` (records patient-reported missed medicine; requires CHW follow-up) or `I have chest pain and cannot breathe` (creates urgent human review).
6. Stop automation with **Stop**.

## Limits and safety

- No actual SMS gateway or real phone connected.
- No patient consent-management system: 'consent' is fictional opt-in for the demonstration.
- Fixed, prewritten routine English / Kiswahili templates only. Language needs human review, Kirundi not yet supported.
- No autonomous diagnosis, treatment, emergency disposition, or changes to medication.
- Urgent and uncertain input is escalated to a human; the scheduler never considers such a patient 'safe'.
- Existing human-approval endpoints and dashboards stay available.
- Demo HTTP routes are not authenticated; bind server to `127.0.0.1` and do not deploy on a public network.
- Scheduler runs only while clinic-side laptop is on. Offline software is not the same as offline carrier SMS; actual text delivery needs a modem/gateway and signal.
- This is a software functionality demo, not clinical outcome validation.

## New files

- `agent.py` — opt-in agent scheduler / safe SMS policy / event log.
- `static/agent-demo.html` — Android-style simulated SMS UI.
- `tests/test_agent.py` — agent workflow checks.
- `server.py` — added four `/api/agent/*` endpoints, `/agent-demo` and local background loop.
