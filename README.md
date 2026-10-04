# TWESE CHW AI

An offline engine that amplifies the impact of the community health workers (CHWs) Burundi already has, for **hypertension and stroke prevention**. Built for the Hack-Nation x World Bank "Small AI for Development" hackathon (health track, 3 to 4 October 2026).

> **Thresholds not yet physician-approved. Demonstration only.** Every clinical number in `config/guideline_htn.json` is a placeholder until the physician signs it (`"approved": false`). All patient data is synthetic.

## Problem

A CHW in Burundi has a limited number of hours a week and many patients with high blood pressure. Some of them drift towards a stroke without anyone noticing. Some never text back. Some stop taking their tablets. The paper record and an SMS inbox can't tell the CHW where this week's hours should go.

*"Because of this tool, a CHW in Burundi will spend this week's visits on the patients most likely to have a stroke, and catch rising blood pressure weeks earlier than they would otherwise; we know because [CITATION]."*

## What the AI does, and why a spreadsheet can't

For a CHW with limited hours, the engine does five things:

1. **Reads messy patient SMS into the record.** Patients write "presha 150 na 95, nimekunywa nusu ya dawa". The engine turns that into fields (150/95, medicine taken: partial, symptom: headache). It uses a regex, an intent model and a small local LLM. Four checks run before anything touches the record, and the CHW confirms every field. A spreadsheet can't read Swahili free text.
2. **Catches rising blood pressure early, and flags 10-year risk.** The hackathon focus (4 October) is the borderline and trending patient: the one who moved from 128/82 to 136/87 over weeks, missing a dose here and there, whom nobody catches today. A **borderline** flag fires when three readings in a row sit in the band just under the patient's goal (130-139/85-89 for 140/90), or the readings keep crossing the goal; it asks the patient for a weekly BP text until two readings in a row are clear. It compares each patient with their own baseline (two trend rules that never fire on a single reading), the guideline cut-offs, and the WHO 10-year cardiovascular risk chart for Eastern Sub-Saharan Africa. Severe BP (180/110 or higher) is an emergency with any symptom, and an urgent clinic referral within 2 days without one. FAST signs are an emergency at any BP. Every flag carries a reason and a guideline reference.
3. **Suggests the next action** from a fixed list: routine, recheck in 7 days, visit this week, refer to clinic, emergency, or "not sure, ask a nurse". **Rules first:** the flags give the rule default. **Then the facts in the text:** the local LLM and a phrase list turn the notes and SMS into codes with quotes (missed doses, ran out of pills, measured after exertion...), and a plain table moves the rule default one step when a code applies; the reason quotes the words (for example, "nusu ya dawa", half a dose, moves a recheck up to a visit). Emergencies bypass it. The CHW confirms or overrides each suggestion, and the override is stored as training data.
4. **Puts the CHW's hours where the risk is highest.** It builds a risk score, then a weekly plan that groups visits by village and uses the real visit and travel minutes recorded per village. Silent patients gain points over time, so the plan doesn't forget them.
5. **Checks patient understanding** with one SMS question at a time ("Reply 1, 2 or 3"). A wrong answer brings the question back in 7 days, queues the approved correction, and raises the patient's priority.

There's also a peer-trajectory model. It compares a patient's recent pattern with local patients who went on to a stroke or an admission, and shows the closest peer cases by anonymised id. It switches on only when the local events table reaches `config.trajectory.min_events` (20). The synthetic history now holds 26 events, so it is on.

## How the weekly plan ranks

The ranking itself is plain arithmetic. The AI provides the inputs.

- **Score** = the WHO 10-year CVD risk in percent × 0.5 (the non-laboratory chart for Eastern Sub-Saharan Africa, page 4 of the WHO PDF, in `config/who_risk_eastern_ssa.json`), plus points from the config for each flag, missed medication, each 30 days of silence, an overdue follow-up, a missed appointment, low understanding, and a peer-trajectory flag. So 10% WHO risk alone is medium tier and 20% alone is high. The chart covers ages 40 to 74: patients aged 75 and over are read as 70–74, with a note that the risk is likely higher; under 40 there is no chart value, and the BP rules carry the load.
- **One plan per CHW.** One clinic laptop serves many CHWs; each plan covers only that CHW's patients.
- **Minutes per patient** = the average visit time plus the average travel time for that village, taken from `time_log`. The config defaults are the fallback.
- Visits go to medium and high tier patients, ranked by score per minute, until the hours run out. Once a village is on the route, more visits there add no travel time, so the visits group by village. Then come SMS check-ins for patients with any risk points. Everyone else waits.
- **Emergencies never enter the plan.** They go to a separate escalation list with the approved referral protocol.
- A person approves every plan, and can move any patient between visit, SMS and wait first.

## Guardrails

- **A fixed answer list**, with `not_sure` when the top probability is below `min_confidence` or the top two are closer than `margin`.
- **Emergency rule first.** An emergency flag returns `emergency_now` at once, with no model asked, and the patient goes to the escalation list, outside the plan, with the protocol text from `docs/emergency_protocol.md` in English or French.
- **One step, and only for a reason in the text.** Julia-1 may move the rule default at most one step on the ladder routine, recheck, visit, refer; never down from refer or emergency. A move counts only when removing one note or message would lower the model's choice by at least `julia.decide_min_text_effect`, and the reason quotes that sentence. `julia.decide` switches the model off entirely (rules only).
- **The BP goal is set by people.** 140/90 by default, 130/80 when a supervisor (checked by PIN) or the record import sets the patient's goal to high risk. The engine never sets it.
- **Misses are caught.** Every missed event and every override becomes a review card that a person labels, and the thresholds move only after a replay shows what the change would have caught and what it would have cost. In detail: every override (up or down), every event without a prior flag (with a pre-filled cause: silence, rule, extraction or model), every referral sent home with no change, every SMS field the CHW corrected, and every trajectory miss becomes a row in `error_reviews`. The supervisor labels it (PIN checked). `GET /errors/summary` gives, per rule and per model version, the flags raised, confirmed false positives and false negatives, low-yield visits (a cost, not an error), and the sensitivity and precision proxies. `POST /errors/replay` re-runs the rules on past data with a changed config, in well under a second on the demo data.
- **People confirm everything.** Each extracted field, each action, each weekly plan, each quiz correction (the CHW taps Send), and each model update (a person presses "Run monthly review").
- **The LLM's role is narrow.** It extracts fields and writes a three-line French summary for the CHW. It never picks an action, never gives advice, and never writes text that goes to a patient. Every output passes the four checks (valid JSON, schema, every number appears in the source, codes and drug names from the config lists). A failed check means manual entry, and every call is logged in `llm_log`.
- **Patient-facing text is written by people.** It lives in `content/` (SMS templates, quiz questions and corrections), with sources.
- **No diagnosis, no prescribing, no images.** The engine produces flags, reasons, scores and routing only.
- **The `approved` flag.** The engine returns `approved: false` and the banner text until the physician signs the config.

## Evaluation

See [docs/evaluation.md](docs/evaluation.md). It's regenerated by `python engine/evaluate.py --db data/chw.db --labels data/labels --config config/guideline_htn.json`.

| Model | Test | Result |
|---|---|---|
| SMS intent, TF-IDF | accuracy on 25 message templates never seen in training (101 SMS) | 0.703 (sw 0.764, fr 0.595, en 0.778) |
| SMS intent, Julia-1 zero-shot | same unseen templates | 0.396 (sw 0.218, fr 0.595, en 0.667) |
| SMS intent, **in use** (TF-IDF, Julia-1 when TF-IDF is unsure: 52 SMS) | same unseen templates | 0.713 (sw 0.745, fr 0.649, en 0.778) |
| LLM extraction (hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M) | all fields right, of accepted (270 of 300) | 0.867 (sbp 1.0, dbp 1.0, meds 0.978, symptoms 0.885) |
| LLM extraction | sent to manual entry: all / messy | 0.1 / 0.133 |
| LLM extraction | wrong BP number that passed all checks | 0 |
| Decider, rules only | agreement with gold: text-modifier rows (518) / plain rows (1214) | 0.542 / 0.956 (not_sure 0.0 / 0.0) |
| Decider, rules only | emergencies caught / agreement with physician | 3 of 8 / physician column not filled yet |
| Decider, rules + context codes + table (in use) | agreement with gold: text-modifier rows (518) / plain rows (1214) | 0.542 / 0.956 (not_sure 0.0 / 0.0) |
| Decider, rules + context codes + table (in use) | emergencies caught / agreement with physician | 3 of 8 / physician column not filled yet |
| Quiz free-text replies (julia-1) | rephrased option mapped right / nonsense reply unclear, 135 replies | 0.815 / 0.689 |
| Next-action model (TF-IDF + LR) | held-out accuracy / macro-F1, 496 rows | 0.776 / 0.634 |
| 30-day deterioration (LR on risk components) | AUC, held-out patients | 0.867 |
| Peer trajectories (kNN, min_events 20) | AUC, leave one patient out (1031 windows, 26 events) | 0.768 |
| Peer trajectories | share flagged: patients with / without an event | 0.73 / 0.19 |

The extraction row tests `llm.extract` alone. In the live SMS path (`engine/sms.py`), a regex also reads the numbers, and any disagreement sends the message to manual entry. The CHW confirms every field either way.

**Limits.** The labels are synthetic. The SMS come from about 30 templates per language, and intent is scored on templates never seen in training. Plain-row action labels come from the config rules, so rules-only agreement there is 1 by construction; text-modifier gold comes from how the cases were written. Behind the guardrail, Julia-1 gets 7 of the 30 text-modifier cases right (rules alone: none), but it also moves or says "not sure" on about half of the plain rows, and it never turned an unextracted chest-pain SMS into an emergency. Whether `julia.decide` stays on is a decision for the team and the physician. The 50-row physician sample (`data/labels/physician_sample.csv`, 30 text-modifier rows, filled without seeing the suggestion) is the only clinical check, and it hasn't been filled in yet. None of this is a clinical validation. The deterioration and peer-trajectory models learn from a synthetic process (26 events), so they show the mechanism, not a validated predictor.

### Layer 3: the decider in use is "facts first" with a table (PLAN.md 5b, merged 4 October)

The decider reads the free text as facts, not as prose. The local LLM, helped by a short phrase list in three languages (`config.context_lexicon`), turns each note and SMS into context codes with the exact words that support them (ran out of pills, missed doses, cannot read, lives alone, travelling, measured after exertion with a normal repeat, side effect, new symptom). Only codes from the last 30 days count. The rules give the default action; the table in `config.step_criteria` moves it one step up or down when a listed code is present, and the reason quotes the words. Emergencies bypass it, it never steps down from a referral, and a person confirms. Julia-1 was tested in the same role and lost to the table, so `julia.decide` is false; Julia-1 keeps the quiz replies and the intent fallback. Revert to Design A: `julia.decide_mode` "actions" and `julia.decide` true, restart the server.

The gate, same labels as main (`docs/evaluation_layer3_facts.md`):

| Gate line | Measured | Pass |
|---|---|---|
| Context codes: precision and recall at least 0.9 | precision 1.0, recall 0.90 (held-out phrasings 0.93) | yes |
| Plain rows: agreement with the rule default at least 0.95, not sure at most 0.05 | table 0.956 and 0 (Julia-1 0.986 and 0.014) | yes |
| Text-modifier rows: codes + Julia-1 at least codes + table | table 0.54, Julia-1 0.05, Design A 0.15, rules only 0 | the table wins, so the table decides |
| Emergencies caught: not fewer than main | 3 of 8, the same | yes |
| Physician sample | not filled yet | pending |

Limits: the phrase list and both synthetic phrasing sets were written by the same team, so code recall is flattered; the plain-row labels count the generator's own missed-medicine notes as text that should move the action (488 rows), decided before the re-run so the test is not circular.

### Independent test

A second synthetic set, built separately, checks that the numbers above are not just memory of the development data: seed 2, different patients, readings and SMS numbers, no hand-built demo patient, and text-modifier notes written with phrasings the development set never uses. Every model that learns trains on the development set and is scored on this held-out set; SMS intent is also scored only on templates never seen in training; the peer-trajectory index is built from development patients and scored on held-out ones. Full table: [docs/evaluation_independent.md](docs/evaluation_independent.md).

| Model | Development set | Held-out set |
|---|---|---|
| Lead time: patients flagged before crossing 140/90, median warning (threshold-only: 0) | 30%, 4.9 weeks | 50%, 2.4 weeks |
| SMS intent in use (TF-IDF, Julia-1 when unsure), unseen templates | 0.71 | 0.70 |
| LLM extraction, all fields right / wrong BP numbers accepted | 0.87 / 0 | 0.89 / 0 |
| Decider in use (rules + context codes + table): text-modifier rows / plain rows (rules only: 0 / 1.0) | 0.54 / 0.96 | 0.58 / 0.96 |
| Emergencies caught (same as rules only) | 3 of 8 | 9 of 14 |
| Next-action model: accuracy / macro-F1 | 0.78 / 0.63 | 0.81 / 0.57 |
| 30-day deterioration, AUC | 0.87 | 0.86 |
| Peer trajectories, AUC | 0.77 (leave one patient out) | 0.81 (index from development patients) |

The held-out numbers hold up, so the models are not just remembering the development data. The deterioration and trajectory models score higher on the held-out set because they train on the whole development set there, and because both sets come from the same simulator; read them as "the mechanism generalises to new synthetic cases", not as accuracy on real patients.

Rebuild and rerun it (the labels belong to databases ending 2026-10-03; build the development database the same way, `python data/make_synthetic.py --db data/chw.db --seed 1 --end-date 2026-10-03`, or `evaluate.py` stops and says so):

```
python data/make_synthetic.py --db data/chw_test.db --seed 2 --text-variant test --end-date 2026-10-03
python data/make_labels.py --db data/chw_test.db --out data/labels_test --seed 2 --no-physician-sample
python engine/evaluate.py --db data/chw_test.db --labels data/labels_test --train-db data/chw.db --train-labels data/labels --config config/guideline_htn.json --out docs/evaluation_independent.md
```

Both sets come from the same simulator, so this shows generalisation to new synthetic cases, not to real patients.

## Data sources, and what they don't cover

All data is synthetic: `data/make_synthetic.py` (60 active patients in 6 villages over 6 months, followed by one CHW, plus 30 former patients over the 6 months before that, 18 of whom had a stroke or admission; former patients feed only the peer-trajectory history and never appear on the dashboard or in the plan). 30 "text-modifier" cases have a CHW note or a patient message that should move the action one step (exertion, travelling, pills ran out, lives alone and cannot read, swollen feet, and chest pain in an SMS the extraction missed). For larger tests, `--patients 150000 --patients-per-chw 150` makes a clinic-sized database with about 1,000 CHWs. It writes `synthetic = true` into the meta table. It doesn't cover real Burundi BP distributions, real SMS language, real event rates, or Kirundi SMS. Sources and the review status of every content file are listed in [docs/sources.md](docs/sources.md).

## One laptop for the whole clinic

The engine is built to run on one clinic laptop for all CHWs. Measured on the development laptop (Apple silicon, 26 GB RAM) with a synthetic clinic of 150,000 active patients, 1,000 CHWs and 3.5 million encounters (`python data/make_synthetic.py --db <file> --patients 150000 --former-patients 20000 --seed 4`):

| Job | Time |
|---|---|
| Weekly batch: flags, peer trajectories, risk scores, escalation list | 8.3 min (flags 2.5 min, trajectories 5.4 min, risk 20 s, escalation 3 s) |
| One CHW's weekly plan | 0.5 to 2.5 s |
| Confirming one SMS (flags, trajectory and risk for that patient) | under 0.1 s |
| Peak memory during the batch / database size | 3.2 GB / 2.5 GB |

The local LLM is the real limit: about 1.7 s per SMS, so about 2,000 messages an hour. Messages from a gateway wait in the inbox and are processed in batches (`POST /inbox/process`). For example, 20,000 hypertension patients each texting every two weeks is about 10,000 messages a week, or about 5 hours of LLM time. On an 8 GB laptop the LLM (3.3 GB) and the weekly batch (3.2 GB) should not run at the same time: run the batch at night, when Ollama has unloaded the model, or use the 1B fallback. This has not been measured on an 8 GB machine yet.

## Privacy

- All data sits in one SQLite file on the CHW's laptop (`data/chw.db`). Nothing is sent anywhere, and the engine binds to 127.0.0.1 only.
- Phone numbers are stored as hashes. Peer cases appear by anonymised id only, and only to the CHW.
- Every change made by a CHW or supervisor is written to the `audit` table.
- Users have a PIN and a role (CHW or supervisor); only a salted hash of the PIN is stored. Supervisor-only routes, such as changing a patient's BP goal, check the role before writing. Create users with `python engine/api.py add-user --name "..." --role supervisor --pin ....`
- Planned before real use: SQLCipher encryption of the file with a passphrase, so a lost device is useless without it. Not built yet; the demo uses synthetic data only.

## Languages

English is the default and the demo language in the frontend. Patient-facing SMS text ships in Swahili, French and Kirundi:

| Language | Status |
|---|---|
| English | Source text |
| Swahili | Drafted, awaiting review by the bilingual teammate |
| French | Drafted, awaiting review |
| Kirundi | Draft, **not yet verified**. The plan: NLLB-200 (`run_Latn`) from French, then a speaker checks it. |

**The Kirundi answer:** a Kirundi SMS can be translated to French with NLLB-200 first, then go through the same pipeline. Stored data are codes, so switching language never changes a record.

## Tech stack

- Python 3.11+, SQLite (one file), pandas, scikit-learn, FastAPI with uvicorn.
- **Julia-1** (SupersonicLabs, 144M parameters, multilingual, about 0.02 s per answer on CPU). It integrates the rules and the free text in the decider (behind the one-step guardrail), reads an SMS only when the TF-IDF intent model is unsure (under 0.7), and maps free-text quiz replies to an option. `config.julia` says which jobs it does; `GET /status` shows them. See the evaluation for how well each works.
- **Ollama** with **MedGemma 1.5 4B** (4-bit GGUF, about 3.3 GB) for extraction and the French summary. It falls back to `gemma3:1b` (about 0.8 GB) when free RAM is below 3.5 GB.
- **React frontend** in `frontend/`, built by the team. It talks to the engine only through HTTP on localhost. The contract is [docs/integration.md](docs/integration.md), and the live version is at http://127.0.0.1:8000/docs.

```
React app (frontend/)  --HTTP-->  engine/server.py (FastAPI, 127.0.0.1:8000)
                                     |
                                  engine/api.py
     signals  decide  sms+llm  risk  plan_week  quiz  followups  hours  trajectory  metrics  retrain
                                     |
                       data/chw.db (SQLite)   config/   content/   models/
```

## Install

```
python3 -m venv .venv
source .venv/bin/activate            (Windows: .venv\Scripts\activate)
pip install -r requirements.txt

# Local LLM: install Ollama (https://ollama.com/download), then
ollama pull hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M
ollama pull gemma3:1b

# Julia-1 (config.julia says which jobs use it; without it the fallbacks run)
python -c "from huggingface_hub import snapshot_download; snapshot_download('SupersonicLabs/Julia-1', local_dir='models/Julia-1')"
python -m pip install -e ./models/Julia-1
```

## Start

```
source .venv/bin/activate
python data/make_db.py --db data/chw.db --schema data/schema.sql
python data/make_synthetic.py --db data/chw.db --patients 60 --months 6 --seed 1
python data/make_labels.py --db data/chw.db --out data/labels --seed 1      (labels match a database ending 2026-10-03)
python engine/risk.py --db data/chw.db --config config/guideline_htn.json
python engine/api.py add-user --name "Supervisor" --role supervisor --pin <4+ digits>     (once, for supervisor routes)
uvicorn engine.server:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000/health (it should say ok) and http://127.0.0.1:8000/docs. Start the React app in `frontend/` with `npm run dev` (or `npm start`). Both run locally, so the demo works with WiFi off.

Other commands:

```
python engine/plan_week.py --db data/chw.db --config config/guideline_htn.json --chw 1 --week-start 2026-10-05 --hours 20
python engine/retrain.py --db data/chw.db --labels data/labels --out models      (the monthly review; a person runs it)
python engine/evaluate.py --db data/chw.db --labels data/labels --config config/guideline_htn.json
python data/import_records.py --db data/chw.db --csv data/sample_import.csv
python -m pytest tests -q
```

## Demonstration workflow

The script is [docs/demo_script.json](docs/demo_script.json) (it's also served at `GET /demo/script`). `POST /demo/reset` rebuilds the same synthetic patients, so you can run it again.

1. **Open the application offline.** Turn WiFi off. The dashboard lists 60 synthetic patients, with the banner showing.
2. **Select a patient and review their blood pressure history.** Patient 1 (58, Kirundo, Swahili): BP climbing slowly from 125 to 136, every reading under 140/90; a borderline flag already has her on the weekly SMS watch; medium tier.
3. **Receive or simulate an incoming SMS.** On the virtual phone: *"Habari. Leo presha yangu ni 138/88. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa."*
4. **Use AI to extract information from the message.** Intent bp_report; 138/88, medicine partial, headache; all checks pass. The text and the fields appear side by side, and the CHW taps Confirm.
5. **Display a clinical flag when a rule is triggered.** A trend flag (her last three readings each 10 or more above her baseline of 125), the borderline flag, and the headache. Her chart shows every reading still under 140/90: no threshold has been crossed yet, and this is the patient the tool is for. The tier goes from medium to high. The suggested action is a visit this week: the rule default (recheck) moved up one step because her SMS says "nusu ya dawa" (half a dose), and the screen quotes it. The CHW steps back down to a recheck, "patient travelling, weekly SMS continues", and the override is stored. The danger-signs question arrives on the phone, the patient replies 2 (wrong), the correction is queued, and the CHW taps Send.
6. **Generate a weekly CHW schedule.** Patient 1 now has a visit in the Kirundo group. Changing the hours changes the number of visits. Emergencies sit on the escalation list, not in the plan.
7. **Allow the CHW to review and approve the suggested actions.** Move one patient, then approve the plan. The supervisor presses "Run monthly review", and a new model version (lr-v1) appears with its held-out scores. The next suggestion carries that version (with the synthetic data it may be "not sure", which sends the CHW to a nurse).
8. **Switch the application's language.** Switch to French, then Swahili. Stored data doesn't change, and Kirundi is marked not yet verified.

## Repository layout

```
CLAUDE.md, PLAN.md     instructions and the build plan
config/                guideline_htn.json (all cut-offs, weights, model names, action criteria), who_risk_eastern_ssa.json (WHO chart)
content/i18n, quiz     patient-facing SMS text and the education question bank (written by people)
data/                  schema.sql, make_db.py, make_synthetic.py, make_labels.py, import_records.py, sample_import.csv
engine/                api.py, server.py and one file per job
docs/                  integration.md, evaluation.md, demo_script.json, emergency_protocol.md, sources.md, video_script.md
tests/                 one test file per step: python -m pytest tests -q
frontend/              the React app (built by the team)
```
