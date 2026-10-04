# Open-ended SMS and separate urgency classification

## Updating your existing working Mac project

Stop Compass with Control+C and back up your working folder. Copy these files from the new ZIP into that folder:

- agent.py
- core.py
- local_llm.py
- urgency_ai.py (new)
- static/app.js
- static/agent-demo.html

Keep your existing .venv and encrypted private_data. No new Python dependencies are required. In the updated project folder, with Ollama open, run:

```bash
export COMPASS_LOCAL_MODEL=medgemma:4b
export COMPASS_LOCAL_TIMEOUT=60
.venv/bin/python launch.py
```

Use `gemma3:4b` instead if that is the model you downloaded. The model name is configurable; this update does not download weights or fine-tune either model. It does not make local inference faster.

Open http://127.0.0.1:8765/agent-demo and http://127.0.0.1:8765/ in separate tabs. Enroll a routine fictional patient. Try:

1. `I keep forgetting my medicine.`
2. `I work nights, so mornings are difficult.`
3. Answer the follow-up in your own words.

When a valid local-model response is available, the reply comes from that response rather than a forced numbered menu or automatic quiz. Symptoms and other concerns can still create a human-review task while the conversation continues. Urgent warnings override generated wording. If inference fails, the original controlled rules/menu fallback still operates and is identified as fallback.

The phone immediately displays a pending message while waiting, disables duplicate submission, and preserves the typed text on failure. The CHW dashboard's SMS thread refreshes after the reply completes.

## Two independent components

1. Conversation: configured local Ollama model, with the current message and up to eight recent messages. It is instructed to ask one relevant question, address the latest reply, avoid repetitive menus, and not diagnose or recommend treatment. Generated replies for symptoms may collect a description and timing; the care team makes clinical decisions.
2. Urgency: separate character TF-IDF + logistic regression binary classifier trained on 40 fictional English examples (20 urgent, 20 nonurgent). Scores >=0.60 flag urgent; scores <=0.40 propose nonurgent; intermediate scores abstain as uncertain. These thresholds are experimental, not clinically validated. Short inputs and non-English patient languages abstain.

Classifications, scores, and model version are persisted per incoming message. The UI displays urgent/nonurgent/uncertain alongside the separate overall patient priority. The score is uncalibrated, not a clinical probability. Unknown words or negation can still be misclassified; this small training set is a starting point for evaluation, not evidence of reliability.

Urgency decisions also retain known danger-phrase rules, local-model urgent suggestions, BP/history rules, and unresolved urgent review tasks. Nonurgent SMS never clears an existing urgent patient task. Open urgent tasks contribute to both patient assessment and dashboard urgent counts. Patient-level prioritization is therefore broader than this text classifier alone.

## Current limits

Classifier accuracy, sensitivity, specificity, and clinical safety have not been established on an independent clinician-labeled set. The classifier is not automatically retrained by the existing intent-feedback feature. Negation is not clinically validated. English/Kiswahili conversation wording and generated-treatment safeguards need real evaluation. Supplemental text checks reject some dosing/treatment instructions, but are not a complete safety guarantee.

Software tests use mocked local-model responses. Real generated reply quality and speed must be assessed on your Mac. The existing database still rewrites encrypted snapshots, SMS remains simulated, and no 150K active-patient validation has been completed.

The next validation is to run the same clinician-labeled messages through each version and compare missed urgent cases, false urgent alerts, correct actions, relevant replies, and latency. Keep evaluation cases separate from the synthetic examples in urgency_ai.py.
