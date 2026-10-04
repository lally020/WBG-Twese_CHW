# OPPQRRST pain history

This update adds structured pain-history intake for human review. It does not establish that a pain complaint is nonurgent or provide a diagnosis.

## Update your current working folder

Stop Compass with Control+C, back up the working folder, and replace:

- agent.py
- core.py
- pain_history.py (new)
- static/app.js
- static/agent-demo.html

Keep the existing .venv, private_data, local_llm.py and urgency_ai.py from the open-ended SMS build. No new dependencies are required.

With Ollama open, start the updated app using your configured model:

```bash
export COMPASS_LOCAL_MODEL=gemma3:4b
.venv/bin/python launch.py
```

Use medgemma:4b instead if that is the downloaded model you wish to run. For a newly extracted folder with no .venv, create one and install requirements.txt first:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Try the pain workflow

Open http://127.0.0.1:8765/agent-demo. Select a routine fictional patient. Send:

`Dull pain in my right knee since yesterday`

It should recognize the explicitly supplied quality, region, and onset, and ask what makes the pain worse. Continue with:

- `Walking makes it worse`
- `Rest helps`
- `It stays in the knee`
- `3`
- `It comes and goes`

The resulting summary appears in the phone demo, patient details on the dashboard, and the pain_history task under Human approvals. All values are patient reported and require clinical interpretation. Fields: onset, provocation, palliation, quality, region, radiation, severity, timing.

For a separate test on another routine fictional patient, start with `My knee hurts`, then send `Now I cannot breathe`. The questionnaire should stop, the intake should be marked interrupted_urgent, and the dashboard should show an urgent review task. Initial chest-related complaints also bypass the questionnaire. These tests must use fictional records.

## Implementation

The questionnaire is controller-driven, with approved English and Kiswahili question text. It asks one field at a time, preserves answers verbatim, and skips a limited set of explicit English phrases already supplied. Numeric severity accepts 0–10; invalid values are re-asked. `cancel` or `stop` ends a nonurgent-flagged interview without deleting collected information or resolving its human-review task.

Pain-history state and field data are stored in encrypted SQLite snapshots, linked to a review task. An incomplete intake can resume after an application restart. A scheduler does not overwrite an active pain question with routine medication outreach.

Urgency screening checks known danger phrases, conservative chest-related/sudden-severe-pain stop rules, the experimental urgency classifier, and the patient's existing urgent BP/review flags before questions and after each answer. The initial complaint is included in subsequent classifier input. A negative classifier result does not clear any urgent flag. Because this is structured controller intake, it does not wait for an LLM response. General open-ended conversation still uses the configured local model outside this pain workflow.

## Limits and evaluation

No detected red flag is not proof of safety. Negation may trigger conservative false alerts (for example, mentioning chest symptoms even to deny them). Literal English extraction is limited; Kiswahili questions have not been independently linguistically validated. Answers are not clinically interpreted. More complex corrections, multiple simultaneous pain complaints, patient demographic risk modifiers, and missing history require clinician review. Neither the urgency classifier nor this pain workflow is clinically validated.

Reference dimensions for pain history are supported by Clinical Methods' discussion of symptom chronology, location, quality, aggravating/alleviating factors and associated manifestations: https://www.ncbi.nlm.nih.gov/books/NBK349/

Chest-related escalation is conservative demonstration logic, not a validated clinical algorithm. Background reference: https://www.nhs.uk/conditions/chest-pain/
