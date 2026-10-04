# CLAUDE.md

Claude Code reads this file every time it starts. Follow it.

## The project

TWESE CHW AI: an offline engine that amplifies the impact of the community health workers (CHWs) Burundi already has, for hypertension and stroke prevention. It reads messy patient SMS into the record, catches rising blood pressure early, suggests the next action per patient, puts the CHW's limited hours where the risk is highest (risk score plus weekly plan), and checks patient understanding with short SMS questions. Built for the Hack-Nation x World Bank "Small AI for Development" hackathon. The full plan is in PLAN.md. Read it before any step. docs/build_instructions.md lists the five product sections; where it differs from PLAN.md, PLAN.md wins.

One condition only: hypertension and stroke prevention. Diabetes is postponed. Blood sugar is stored because it is in the record, and never used.

The frontend already exists: a React app in frontend/ in this repo, built by the team. Never edit anything under frontend/. Do not build screens, pages or Streamlit apps. This repo's Python side is the engine and the data layer. Every feature ends as a function in engine/api.py that returns plain JSON, exposed by engine/server.py (FastAPI on 127.0.0.1:8000) for the React app to call.

Not in scope: CHW education, lessons or microtraining for the workers. Patient education questions by SMS are in scope (PLAN.md step 10).

## Who you are helping

Four people who are new to coding. Explain each step in plain words. Do not skip basic steps. Before you write or change files, say in two lines what you will create or change, then wait for "go".

## Stack

- Python 3.11 or newer, in a .venv
- SQLite through the standard sqlite3 module, one file: data/chw.db
- pandas, scikit-learn
- Julia-1 (SupersonicLabs/Julia-1) on CPU, in models/
- Ollama with a small local LLM (layer 4), part of the core. It only extracts fields from text and writes short French summaries from structured fields.
- engine/api.py: plain Python functions returning JSON-serializable data.
- engine/server.py: FastAPI with uvicorn, one route per api function, bound to 127.0.0.1:8000, CORS for http://localhost:3000 and http://localhost:5173, a /health route. Nothing else in it: no logic, only calls into engine/api.py. The virtual phone in the React app uses GET /phone/{patient_id}, POST /messages/incoming and POST /messages/{id}/sent; keep those three stable.
- Packaging (PLAN.md step 15) is PyInstaller onedir, only after the video exists. Do not start it on your own.

Do not use: Docker, cloud APIs, or anything that needs the internet at run time. Do not add a second web framework.

## Rules for code

- The simplest solution that works. Short functions. Comments in plain English.
- Every script takes its settings from command line arguments with argparse: --db, --config, --patient, --seed, and so on. No hard-coded paths.
- No hard-coded clinical numbers. Read config/guideline_htn.json. The engine returns config.approved so the frontend can show the "not yet physician-approved" banner.
- Outputs are a fixed answer list from the config, plus a reason text and a guideline reference. The tool never writes free medical advice.
- Decider: rules first, then Julia-1. Julia-1 never sees a bare reading; it reads the flags, the rule default, and the CHW notes and patient messages verbatim, with the option criteria from config.action_criteria. It may move the rule default at most one step on the ladder (routine, recheck_7_days, visit_this_week, refer_clinic), never down from refer_clinic or emergency_now, and emergencies bypass it. Always include not_sure and the confidence rule. The output names the sentence that moved the decision.
- Emergencies (a flag with emergency = yes, or the action emergency_now) go to the escalation list with the protocol text from config, never into the weekly plan. The tool never diagnoses, never prescribes, and never overrides a config rule. Every extracted field, every action and every plan is confirmed by a person in the frontend.
- Patient-facing text (SMS templates, quiz questions, corrections) comes from content/i18n and content/quiz, written by people. Never generate patient-facing text with the LLM. Julia-1 may only map a free-text reply to one of the fixed options or "unclear".
- LLM rules: all calls go through engine/llm.py. Temperature 0, JSON format, short max_tokens. Check every output: valid JSON, schema, every number appears in the source text, symptom codes and drug names from the config lists. A failed check means manual entry, never a guess. Log every call to llm_log. The LLM never picks an action and never writes advice. Pick the fallback model when free RAM is low.
- Store model_version and config_version with every decision.
- Synthetic data only. Write synthetic = true into the meta table. Never commit data/chw.db or models/.
- One test file per step in tests/. Run `python -m pytest tests -q` after each step.
- For a command that takes more than a minute, add a notification at the end: on Mac `; say "done"`.

## Wording

Use "flag", "needs review", "suggested action". Never "diagnosis" and never "the patient has".

## Git

Pull before you start. Commit after each step with a one-line message. Push when the step works. Never force-push.

## How to run

```
source .venv/bin/activate
ollama list            (Ollama must be running; the model named in config.llm.model must be listed)
python data/make_db.py --db data/chw.db --schema data/schema.sql
python data/make_synthetic.py --db data/chw.db --patients 60 --months 6 --seed 1
python engine/risk.py --db data/chw.db --config config/guideline_htn.json
python engine/plan_week.py --db data/chw.db --config config/guideline_htn.json --chw 1 --week-start 2026-10-05 --hours 20
python -c "import engine.api as a; print(a.status())"
uvicorn engine.server:app --host 127.0.0.1 --port 8000     (then open http://127.0.0.1:8000/docs)
```
