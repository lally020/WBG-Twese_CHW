# Compass automatic evaluation and improvement demo

## Install

Download `CHW_Compass_Auto_Learning_Demo.zip` into Downloads. In the main-app Terminal tab, press Control-C. Then:

```bash
cd "$HOME/Downloads/compass_agent_build"
unzip -o "$HOME/Downloads/CHW_Compass_Auto_Learning_Demo.zip" -d .
.venv/bin/python -m pip install -r requirements-eval.txt
.venv/bin/python launch.py
```

Reuse the existing database passphrase. Open **http://127.0.0.1:8765/eval-demo**. Keep the main app local; do not tunnel port 8765. This patch replaces the urgency-classifier module, evaluation modules, server routes and selected static pages. It preserves the patient database and Twilio configuration. It does not change clinical rules or the conversational LLM.

The first automatic cycle begins at startup. It evaluates, trains candidates, tests the selected candidate, activates it if the gates pass, then reruns the development benchmark. Allow roughly 4–6 minutes for the first complete cycle on similar hardware; inspect Run progress. Candidate training itself is much shorter than the two full evaluation runs. A saved, clearly labeled example shows the measured build-environment result before your own cycle finishes.

## What is automatic

- Startup and saved application/test changes trigger evaluation while the service runs.
- Labeled **development** failures provide additional training examples for urgency classification.
- The loop trains nine TF-IDF / logistic-regression candidates: three regularization values × three urgent thresholds. It keeps the uncertainty band and unsupported-language abstention.
- It selects exactly one candidate using development results. Release-set results do not select candidates or tune parameters.
- It compares that candidate with the active model on **20 separate frozen synthetic release cases**, checks message workflows and conversations, and reruns 16 patient-priority policy checks.
- Passing candidates activate automatically for the fictional demo. Failing candidates leave the current model in place. No approval click is required for an accepted demo candidate.
- The model specification persists in `evaluation/model_registry/active.json`; the next inbound message loads it automatically. No server restart is needed for that model swap.
- The previous specification and cycle history are retained. **Roll back previous model** restores the previous version and pauses automatic improvement.

Unchanged rejected inputs reuse their prior decision rather than repeatedly trying to obtain a passing result. Changes are debounced and duplicate jobs are prevented. The loop does not run continuously when nothing changes. It does not label new messages using its own predictions and call those labels truth.

## Activation checks

The frozen-release comparison must show strictly fewer urgency false negatives, improved urgency F1, no additional classifier false positives, and no newly missed previously detected urgent cases. Full-workflow misses and false alarms cannot increase. Conversation review actions and previously detected urgent cases must be retained. Replies must be nonempty and persisted, processing must stay within the five-second rules-mode budget, and main priority-policy checks must pass.

These are **relative engineering improvement checks for a fictional demo**, not clinical acceptance thresholds. A candidate can improve and be accepted while still making errors or missing the broader development benchmark targets. The page displays these separately.

The automatic gate currently tests the rules-mode message workflow. If `COMPASS_LOCAL_MODEL` is configured in the app environment, automatic activation is blocked because that conversational engine was not covered by these release checks. Candidate metrics can still be inspected. Testing and supporting that engine in the activation gate is additional work; do not hide this by claiming the LLM was evaluated.

## Measured example

One end-to-end build-environment cycle on the separate synthetic release set:

| Metric | Incumbent | Selected candidate |
|---|---:|---:|
| Classifier expected urgent cases flagged | 5/10 | 9/10 |
| Classifier false alarms among nonurgent cases | 0/10 | 0/10 |
| Full-workflow expected urgent cases flagged | 5/10 | 9/10 |
| Full-workflow false alarms among nonurgent cases | 2/10 | 2/10 |

The remaining missed urgent case and two full-workflow false alarms remain failures. Do not describe this as 90% clinically validated sensitivity. The dataset is small, synthetic, authored for this prototype, and has proposed unreviewed labels. A near-paraphrase relationship can remain even though exact training overlap is checked. Repeated use of this release set is not repeated independent external validation.

## Development versus release data

`evaluation/messages.jsonl` contains 32 **development** cases. After automatic learning starts, their labels and failure examples may enter training. Improvements on them are post-training development results, not held-out performance.

`evaluation/release_gate.jsonl` contains 20 separate frozen cases. Exact overlap with original or added training text is rejected. A pinned SHA-256 digest prevents a changed release file from silently becoming a new passing benchmark. Introducing a new release dataset requires explicitly updating and reviewing the protocol; do not change its labels merely to pass.

`evaluation/conversations.jsonl` and `evaluation/patients.jsonl` provide workflow regression checks. The patient targets test existing demonstration policy, not the clinical validity of its BP thresholds.

Review forms save separate feedback records with reviewer and rationale. The automatic loop currently consumes the fixed labeled development dataset; it does not consume those review notes automatically. Apply adjudicated changes to development labels explicitly. New real-world labels need human clinical adjudication; this tool cannot manufacture trustworthy ground truth.

## What does not learn automatically

The main BP-priority rules, pain-history controller, conversational LLM, and clinical thresholds are not rewritten. The urgency classifier improves the urgency decision in patient messaging and can thereby affect dashboard priority through existing workflow logic. Automated evaluation does not guarantee improvement on each run; rejection is a valid result.

The older medication-barrier **intent classifier** remains separate: verified label corrections trigger its candidate training/evaluation. Its existing promotion flow uses a small synthetic intent benchmark, not these urgency gates, and promotion is in-memory. The new urgency registry is persistent.

## Present the demo in one minute

1. Open Evaluation Lab. Point to the automatic loop at the top and its live active-model version.
2. Show before/after on the **separate frozen cases**, including remaining false alarms. If a recorded build example is shown, identify it as recorded, not a completed run on your Mac.
3. Expand activation checks. Explain that candidates are trained from development failures, but selection finishes before the separate release comparison is scored.
4. Show the lower development confusion matrix and one failed message. Explain that these failures guide future iterations; a high training/development score alone cannot approve a candidate.
5. Show Rollback and the saved previous version. Do not click it unless you want to restore the prior model and pause learning.

Suggested explanation: “Our demo evaluates the current classifier, learns from labeled development failures, tests a candidate on separate cases, and automatically activates a measured improvement. It preserves the previous model when checks fail. These are synthetic engineering results; clinical validation requires representative, independently adjudicated data.”

## Commands and files

Run the benchmark alone:

```bash
.venv/bin/python run_evals.py --baseline evaluation/baseline_rules/report.json
```

Run the bounded improvement cycle directly:

```bash
.venv/bin/python auto_improve.py
```

`run_evals.py` alone does not activate models; the server automatically chains it to `auto_improve.py`. Do not launch an extra command-line improvement cycle while the web cycle is running.

Reports are versioned under `evaluation/runs/`, with source/dataset hashes, pinned urgency-model specification, JSON and CSV outputs, metrics, and latency. A single pinned specification is used throughout each evaluation run. The pipeline exits nonzero if configured benchmark checks fail or a requested baseline comparison has new failures/incompatible data. This CLI gate is separate from the incremental fictional-demo activation gate.

Automatic activation decisions and specifications are under `evaluation/model_registry/`. There are no serialized executable model pickles; models are rebuilt from validated bounded JSON specifications. The startup settings file remembers when automatic improvement has been disabled or paused after rollback. The file watcher is session-scoped.

`evals.yaml` defines suites, score thresholds, timing and an optional local Ollama `LLMEvaluator`. The judge is disabled by default, has not been evaluated in these results, and never supplies urgency reference labels. The auto-learning release checks use their explicit fixed protocol in `auto_improve.py`; editing YAML targets does not weaken those checks.

## Verification and limits

The implementation was checked against 64 existing application tests, 21 evaluator/dashboard/automation tests, and an isolated full service cycle. Tests cover live model reload, corrupt-spec retention, invalid parameters, language abstention, rejected regressions, rollback, single-cycle locking, background file watching and confusion-matrix arithmetic.

No real SMS was sent by evaluations. Timing excludes SMS transport and carrier delivery. Model probabilities remain uncalibrated. There is no clinician-adjudicated prospective dataset, external clinical validation, production model registry, or live-outcome monitoring in this demo. Auto-learning should remain restricted to this fictional demonstration until those are established.
