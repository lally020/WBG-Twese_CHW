# TWESE CHW AI: build plan (engine and data)

Hack-Nation x World Bank, health track. Competition weekend: 3 to 4 October 2026.
Repo: https://github.com/lally020/KS

This file is the technical blueprint for the engine and the data layer. The frontend already exists and is built by the team separately; this plan does not build screens. docs/build_instructions.md lists the five product sections and the design rules for that frontend. Where the two differ, this file wins, and the "Reconciled decisions" block below records why.

## 1. What we build

An offline engine that amplifies the impact of the community health workers (CHWs) Burundi already has. It runs on a laptop with 8 GB of RAM or less. One condition: hypertension and stroke prevention. Diabetes is postponed; blood sugar is stored because it is in the record, and not used. More conditions come later through the config file.

For a CHW with limited hours, the engine does five things: it reads messy patient SMS into the record, it catches rising blood pressure early, it suggests the next action for each patient, it puts the CHW's hours where the risk is highest, and it checks over time whether each patient understands their condition and treatment.

The focus for the hackathon, decided 4 October: the patients who are borderline or trending in the wrong direction. Not the emergencies. A patient at 182/112 with chest pain will reach the clinic with or without this tool; the patient who moved from 128/82 to 146/92 over eight weeks, missing a dose here and there, is the one nobody catches today, and that is the one the demo, the metrics and the remaining build hours are about. Emergency handling stays as it is (bypass, escalation box, protocol) and does not need to be perfect this weekend; the keyword safety net for danger words in raw text moves to the "later" list in section 7.

Not in scope: CHW education and microtraining. Patient education questions by SMS are in scope. Screens are not in scope: the frontend exists, and the engine exposes functions that return JSON for it (step 5).

The five product sections, and where each lives in this plan:

- A. Patient dashboard: the frontend, fed by engine/api.py list_patients and patient_detail (step 5).
- B. AI-assisted monitoring: steps 3, 4, 6 and the record import in step 6.
- C. SMS communication: step 6 (inbox processing, extraction, confirmation), step 7 (reminders and follow-up messages), step 10 (education questions).
- D. Weekly CHW planner: steps 7, 8, 9. Emergencies are handled outside the planner.
- E. Patient education and follow-up: step 10 and the metrics in step 11.

The demo loop, end to end:

1. A patient sends an SMS in Swahili.
2. The engine reads it: Julia-1 names the intent, the local LLM (layer 4) pulls the numbers, the dose and the symptoms into fields, the code checks them against the source text, and the frontend shows the text and the fields side by side for the CHW to confirm. The record updates.
3. The engine checks the patient against baseline, trend and guideline cut-offs, and raises a flag with a reason.
4. The patient's risk score updates, and the patient moves up this week's plan. An emergency flag goes to the escalation list instead, not into the plan. When enough local outcomes exist, a peer-trajectory flag says whether local patients with the same pattern went on to an event, and shows them.
5. The engine suggests one action from a fixed list, or says "not sure, ask a nurse".
6. The CHW confirms or overrides in the frontend. The choice is stored.
7. The engine schedules the follow-up, queues the reminder SMS, and logs the time.
8. The patient gets one education question by SMS ("Reply 1, 2 or 3"). The answer updates their understanding score. A wrong answer queues the approved correction for the CHW to send with one tap.

Problem statement for the video, in the brief's template (page 10):
"Because of this tool, a CHW in Burundi will spend this week's visits on the patients most likely to have a stroke, and catch rising blood pressure weeks earlier than they would otherwise; we know because [CITATION]."

Architecture: see the infographic (three boards: architecture, options, learning loop).

### Reconciled decisions (build instructions vs this plan)

- Name: TWESE CHW AI.
- One condition: hypertension and stroke prevention. Diabetes is postponed to a later version. No glucose flag, no diabetes points. Blood sugar stays in the record as a stored field.
- Frontend: already built by the team in React, in frontend/ in this repo. This plan builds the engine and the data layer only. Step 5 defines the integration contract: engine/api.py holds the functions, and engine/server.py (FastAPI, localhost only) exposes them as JSON endpoints that the React app calls. The design rules in the build instructions apply to that frontend.
- Languages: English is the default and the demo language in the frontend. The engine ships patient-facing text in Swahili (verified by the teammate), French (verified) and Kirundi (machine-translated with NLLB-200 and marked "not yet verified"). Screen strings belong to the frontend. Stored data are codes and never change with the language.
- Emergencies: a patient whose flag or suggested action is emergency_now is returned in a separate escalation list with the approved referral protocol text. Emergencies never enter the hours allocation.
- Clinical thresholds: the config carries "approved": false until the physician signs it. The engine returns that flag so the frontend can show the banner "Thresholds not yet physician-approved. Demonstration only." The README says the same.
- Record import: a CSV import of the existing record fields is part of step 6. The demo uses synthetic records only.
- Reminders and follow-up messages: templated outgoing SMS, queued offline, sent on Sync. Step 7.
- Build order: monitoring, SMS, planner first (steps 1 to 9), then education questions (step 10), then extra languages (step 12). Same as the floor and bonus order below.
- Demo: a virtual phone inside the React app texts the engine, so the video shows a patient's SMS, the CHW's confirmation and override, the quiz answer, and the monthly review changing the model version, all in one take. Step 13.
- Packaging: the models already run natively on the laptop's hardware (Ollama and Julia-1 are native processes; the browser only draws the screens), so an exe gives no model speed. It gives one-click start and a USB install story. Step 15, only after the video is recorded.

## 2. One-time setup, each person, about 30 minutes

Do these steps in order. Mac steps first. Windows notes in brackets.

1. Install VS Code: https://code.visualstudio.com
2. Install Git. Mac: open the Terminal app, type `git --version`, press Enter. If a window asks to install "command line developer tools", click Install. [Windows: https://git-scm.com/download/win]
3. Install Python 3.11 or newer: https://www.python.org/downloads. Check: `python3 --version`. [Windows: tick "Add Python to PATH" in the installer.]
4. Install Claude Code. Mac, in Terminal: `curl -fsSL https://claude.ai/install.sh | bash` [Windows, in PowerShell: `irm https://claude.ai/install.ps1 | iex`]. Then in VS Code, open the Extensions view (the four-squares icon on the left) and install "Claude Code".
5. Sign in to GitHub inside VS Code: click the Accounts icon (bottom left) and choose "Sign in with GitHub".
6. Get the code.
   - Jake, first time: the folder "WBG Hackathon" already holds a git repo that points at github.com/lally020/KS. In VS Code: File, Open Folder, pick "WBG Hackathon". Open the Source Control view (the branch icon on the left). Click the "+" next to Changes. Type the message "Plan and project instructions". Click Commit. Click "Publish Branch". If VS Code says the remote has changes you do not have: Terminal, New Terminal, then run `git pull origin main --allow-unrelated-histories`, then click Sync.
   - Teammates: Jake adds you first (GitHub repo, Settings, Collaborators). Then in VS Code: View, Command Palette, type "Git: Clone", paste `https://github.com/lally020/KS.git`, pick a folder, open it.
7. Make the Python environment. In VS Code: Terminal, New Terminal. Then:
   - `python3 -m venv .venv`
   - Mac: `source .venv/bin/activate` [Windows: `.venv\Scripts\activate`]
   - `pip install -r requirements.txt` (this file appears after build step 1)
8. Install Ollama (runs the local LLM): https://ollama.com/download. Open it once so it runs in the background. Then in the terminal pull the models, official tags: primary `ollama pull gemma3:4b` (about 3.3 GB, the name in config.llm.model) and fallback `ollama pull gemma3:1b` (about 0.8 GB, used when RAM is short). The team moved from MedGemma 1.5 4B to Gemma 3 4B on 2026-10-04. One person downloads; the others copy the files by USB if the internet is slow, which is also the side-load story for the video.
9. Start Claude Code: in the same terminal, type `claude` and press Enter. First message to type: "Read CLAUDE.md and PLAN.md. Then do build step 1."

## 3. Folder layout

```
KS/
  CLAUDE.md          instructions Claude Code reads every time it starts
  PLAN.md            this file
  requirements.txt   Python packages
  data/              schema.sql, make_db.py, make_synthetic.py, make_labels.py, import_records.py, chw.db   (person 1)
  data/labels/       synthetic labeled sets and physician_sample.csv   (person 1 + the physician)
  engine/            api.py, signals.py, decide.py, llm.py, sms.py, hours.py, risk.py, plan_week.py, quiz.py, evaluate.py, retrain.py (person 2, person 3)
  config/            guideline_htn.json, who_risk_eastern_ssa.json   (person 2 + the physician)
  content/i18n/      sw.json, fr.json, rn.json: patient-facing SMS text; sw_examples.json; sms_templates.json   (person 4)
  content/quiz/      patient_htn.json: questions, options, correct answer, correction text, source, in sw, fr, rn, en   (person 4 + the physician)
  models/            Julia-1 download, not in git
  tests/             one small test file per step
  docs/              build_instructions.md, README draft, video script, demo_script.json, sources.md, evaluation.md, integration.md  (person 4)
  frontend/          the React app, built by the team, in this repo; it calls engine/server.py over HTTP on localhost
```

The React app and the engine live in the same repo but never in the same folder. The frontend team works in frontend/ only; the engine team never edits it.

Person 3 now owns engine/api.py and the integration with the frontend, instead of screens. Person 4 writes docs/integration.md with person 3.

Rule: each person works inside their own folder. `git pull` before you start. `git push` when a step works. Small commits.

## 4. Data model (SQLite, one file: data/chw.db)

The existing record has these fields per encounter: sex, age, complaint (free text), number of children, tests performed (multi-choice), positive results (multi-choice), referral (multi-choice), doctor's recommendation (free-text prescription), additional doctor's notes (free text), BP, blood sugar, height, weight, smoking status. New fields: CHW notes and patient SMS messages. The tables below keep every existing field under its own name and add only what the engine needs. make_synthetic.py and import_records.py must match them exactly.

- patients: id, sex, age, number_of_children, smoker (yes, no), height_cm, weight_kg, village, phone_hash, language, enrolled_on, condition_codes (taken from positive results, for example hypertension)
- encounters: id, patient_id, date, source (clinic, chw, sms), complaint_text, tests_performed (codes), positive_results (codes), referral (codes), doctor_recommendation_text, doctor_notes_text, chw_notes_text (new), bp_text (as recorded, for example "140/90"), sbp, dbp (split from bp_text), blood_sugar, blood_sugar_unit (stored, not used), meds_taken (yes, no, partial, from SMS or the CHW), symptom_codes (extracted from complaint, notes or SMS by layer 4 and confirmed by the CHW), lang
- medications: id, patient_id, encounter_id, name, dose_text, source_text (extracted from the doctor's recommendation by layer 4, names checked against the config list, confirmed by the CHW)
- messages: id, patient_id, direction (in, out), kind (report, reminder, follow_up, quiz, correction, other), lang, text, received_at, parsed_json, encounter_id (set when a message becomes an encounter row), status (queued, sent)
- flags: id, patient_id, date, type (threshold, trend, symptom), value, reason_text, guideline_ref, emergency (yes, no)
- risk_scores: id, patient_id, date, score, tier (low, medium, high), components_json, config_version
- plans: id, chw_id, week_start, patient_id, action (visit, sms, wait), rank, est_minutes, reason_text, confirmed, done_on
- decisions: id, patient_id, date, state_json, suggestion, probs_json, final_choice, override_reason, outcome_at_next_contact, model_version, config_version
- followups: id, patient_id, due_date, kind (chw_visit, clinic_appointment, sms_check), status (due, missed, done), done_on. Missed means due_date is past and done_on is empty.
- quiz_results: id, patient_id, question_id, topic, asked_at, channel (sms, visit), answer, correct (yes, no, unclear), lang, next_due
- events: id, patient_id, date, type (stroke, hospital_admission, emergency_referral_confirmed, death), source (referral, clinic, chw_note, followup), note. Hard outcomes only; this is what the trajectory model learns from.
- error_reviews: id, patient_id, kind (false_positive, false_negative, extraction_error), source (override_down, override_up, event_without_flag, referral_sent_home, field_corrected, trajectory_miss), linked_id (the flag, decision, event or message), rule_id, model_version, config_version, detected_on, evidence_json, supervisor_label (confirmed_error, correct_call, unavoidable, pending), cause_code (threshold, trend_rule, symptom_rule, extraction, silence, model, chw_judgement, other), reviewed_by, reviewed_on. One row per suspected miss or over-call; the monthly review works from this table.
- trajectory_scores: id, patient_id, date, share_peers_with_event, peer_ids_json, flag (yes, no), reason_text, model_version
- feedback: id, who (chw, patient), target (suggestion, plan), value, note
- time_log: id, chw_id, activity (visit, sms, travel, admin), patient_id, village, start, end
- llm_log: id, message_id, job (extract, medications, summary), model, prompt_text, response_text, valid, reason, at
- audit: id, user_id, action, table_name, row_id, at
- meta: key, value (holds "synthetic = true", schema version, config version)

The decisions table is the training data that grows by itself. The time_log table is the hours feature, and it also feeds the weekly plan with real minutes per visit and per village.

How the existing fields are used:

- BP: stored as recorded in bp_text, and split into sbp and dbp. The baseline, trend and cut-offs use sbp and dbp. If a record holds only one number, it is treated as sbp and the trend uses sbp alone.
- Blood sugar: stored with its unit. Not used in this version.
- Complaint, doctor's notes, CHW notes: free text in any of the languages. Layer 4 extracts symptom codes with the same four checks as for SMS. Julia-1 reads them for the decision state.
- Doctor's recommendation: layer 4 extracts medication names and doses. Names must appear in the source text and in config.medications. The CHW confirms. Adherence replies by SMS are compared to this list.
- Positive results: become condition_codes. The engine works on patients whose codes include hypertension.
- Referral: feeds the "referrals completed" metric and the outcome field in decisions.
- Age, sex, smoking status, height and weight: feed the WHO office-based chart (BMI = weight in kg divided by height in m squared) and so the base of the risk score. The latest weight is used. Number of children is stored and not used yet.

## 5. Guideline config (config/guideline_htn.json)

All cut-offs and weights live here, never in code. The physician sets the numbers, adds the citation for each, and sets "approved" to true. Until then the engine returns approved = false and the frontend shows the banner. The numbers below are placeholders.

```
{
  "config_version": "2026-10-03d",
  "approved": false,
  "approved_by": "",
  "approved_on": "",
  "sources": [
    {"id": "ISH2020", "title": "2020 International Society of Hypertension global practice guidelines", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC8762770"},
    {"id": "PASCAR2017", "title": "Roadmap to achieve 25% hypertension control in Africa by 2025", "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5642030/"},
    {"id": "WHO2019", "title": "WHO cardiovascular disease risk charts, office-based, Eastern Sub-Saharan Africa", "url": "https://www.thelancet.com/journals/langlo/article/PIIS2214-109X(19)30318-3/fulltext"}
  ],
  "baseline_readings": 3,
  "trend_window_readings": 6,
  "thresholds": [
    {"id": "grade2", "sbp_gte": 160, "dbp_gte": 100, "action": "visit_this_week", "source": "ISH2020"},
    {"id": "severe", "sbp_gte": 180, "dbp_gte": 110, "action": "emergency_now", "source": "ISH2020"}
  ],
  "trend": {"rules": [{"sbp_rise_mmhg": 10, "consecutive_readings": 3}, {"sbp_rise_mmhg": 15, "over_readings": 4}], "action": "recheck_7_days", "source": "Design choice; ISH 2020 asks for repeat readings before acting on one"},
  "borderline": {"band_below_goal_mmhg": 10, "consecutive_readings": 2, "crossings_in_window": 2, "window_readings": 6, "action": "recheck_7_days", "ask_weekly_sms": true, "source": "ISH 2020 high-normal band 130-139/85-89 for a goal of 140/90; the counts are a design choice"},
  "symptoms_emergency": ["face_droop", "arm_weakness", "speech_difficulty", "sudden_severe_headache", "vision_loss"],
  "actions": ["routine", "recheck_7_days", "visit_this_week", "refer_clinic", "emergency_now", "not_sure"],
  "emergency_protocol": {"text": "[Approved referral protocol: where to send the patient, whom to call, what to write]", "source": ""},
  "min_confidence": 0.6,
  "margin": 0.15,
  "risk": {
    "base_table": "config/who_risk_eastern_ssa.json",
    "points": {"threshold_flag": 4, "trend_flag": 3, "symptom_flag": 10, "missed_meds": 2, "silent_30_days": 2, "overdue_followup": 2, "missed_appointment": 2, "low_understanding": 2},
    "tiers": {"high": 10, "medium": 5}
  },
  "medications": ["amlodipine", "nifedipine", "hydrochlorothiazide", "enalapril", "lisinopril", "losartan", "atenolol", "methyldopa", "furosemide", "aspirin"],
  "quiz": {
    "ask_after": ["sms_bp_report", "chw_visit"],
    "topics": ["medication", "salt_and_diet", "danger_signs", "when_to_seek_care", "what_bp_means"],
    "repeat_days_wrong": 7,
    "repeat_days_right": 30,
    "low_understanding_below": 0.5,
    "window_days": 90
  },
  "trajectory": {"window_readings": 8, "horizon_days": 90, "k": 10, "min_events": 20, "flag_share_gte": 0.5, "points": 4, "show_peers": 3},
  "capacity": {"hours_per_week": 20, "default_visit_minutes": 30, "default_travel_minutes": 20, "sms_minutes": 3},
  "reminders": {"days_before_appointment": 2, "follow_up_after_visit_days": 3},
  "llm": {
    "model": "hf.co/<repo-name>:Q4_K_M",
    "fallback_model": "gemma3:1b",
    "min_free_ram_gb": 3.5,
    "temperature": 0,
    "max_tokens": 150,
    "output_language": "fr",
    "symptom_codes": ["headache", "vision_change", "chest_pain", "face_droop", "arm_weakness", "speech_difficulty", "dizziness", "none"]
  }
}
```

The base risk is the WHO office-based 10-year CVD risk for Eastern Sub-Saharan Africa. It needs only age, sex, smoking, systolic BP and BMI, no lab (WHO CVD Risk Chart Working Group, Lancet Global Health 2019). The physician and person 2 copy the chart into config/who_risk_eastern_ssa.json as a lookup table. If that table is not ready, the score uses the points only, and the README says so.

Silence counts. A patient who never texts sends no signals, so days since last contact add points. Without that, the plan would forget the quiet patients.

Emergencies are separate. Any flag with emergency = yes, or a suggested action of emergency_now, puts the patient in the escalation list with the emergency_protocol text. The planner never allocates hours to an emergency; the CHW acts on the protocol.

Layer 3 has two designs. Design A below is what runs on main today. Design B, "facts first", is a fork on the branch layer3-facts, described in section 5b, with a gate that decides whether it replaces A. Until the gate is passed, main stays on A and anything recorded or shipped comes from main.

Design A (current, on main). Layer 3, Julia-1, integrates the rules and the free text. It does not compete with the rules and it never does arithmetic: its model card says it cannot supply missing facts or carry calculations, so it must never be asked to rediscover a threshold from a raw reading. The rules run first and hand Julia-1 their output. The state Julia-1 reads is: the flags with their reasons, the rule table's default action, adherence and days silent, and then the CHW notes and the last two or three patient messages verbatim, in whatever language they arrived. Each option in the fixed list carries a one-line criterion from config.action_criteria, which is the input Julia-1 is built to use. The rules stay the floor: emergency flags bypass Julia-1 entirely; for everything else Julia-1 may move the action at most one step up or down the ladder (routine, recheck_7_days, visit_this_week, refer_clinic) from the rule default, and never down from refer_clinic. Low or close probabilities give not_sure. A person confirms. The test for this layer is not agreement with the rules, which would be a tautology, but agreement with the physician on cases where the text changes the right answer (step 2b).

```
  "action_criteria": {
    "routine": "Blood pressure at goal, medicine taken, no symptoms, nothing in the notes or messages that worries the CHW.",
    "recheck_7_days": "Mildly above goal or a trend flag, no symptoms, and the patient can be reached by SMS. Also the right step down when a note explains a high reading, for example measured after exertion with a normal repeat.",
    "visit_this_week": "Grade 2 flag, or missed medication, or a non-emergency symptom with BP above goal, or the text says the patient cannot manage by SMS, for example lives alone, cannot read, or has run out of pills.",
    "refer_clinic": "Severe BP without symptoms, a side effect that needs a prescriber, or the rule default is refer. Never step down from this option.",
    "emergency_now": "Handled by the rules. Choose only if the text describes FAST signs, chest pain, sudden severe headache or sudden vision loss that the flags did not already catch.",
    "not_sure": "The text contradicts the numbers, is unclear, or describes something none of the other options covers."
  },
  "julia": {
    "intent": "fallback_only",
    "decide": true,
    "decide_max_steps": 1,
    "never_step_down_from": ["refer_clinic", "emergency_now"],
    "quiz_reply": true,
    "note": "intent fallback_only means Julia-1 reads a message only when the TF-IDF model's top probability is under 0.7. decide is on behind the one-step guardrail. Re-run engine/evaluate.py before changing any of these."
  },
```

Those two blocks go into config/guideline_htn.json next to the others.

## 5b. Layer 3 fork: Design B, "facts first" (branch layer3-facts)

Why a fork. The evaluation of Design A (docs/evaluation.md, 4 October) showed Julia-1 getting 7 of 30 text-dependent cases right but agreeing with the rule default on only 53 percent of plain cases, with 21 percent "not sure". The cause is the question, not the model: a six-way choice over prose, with raw Swahili in the state and long conditional criteria, asks a 144M encoder to reason, which it cannot do. Design B changes the division of labour so each model does only what it is good at. Because it touches extraction, the state builder and the decider at once, it is built on a branch so main can keep Design A if B fails its gate.

The design. Three stages, in this order:

1. Gemma reads the free text. The extraction call that already runs for every message and note (step 6) also returns context_codes: a list of codes from a fixed vocabulary, each with the quote from the text that supports it. The vocabulary, in config.context_codes: ran_out_of_pills, missed_doses, cannot_read, lives_alone, travelling, unreachable, measured_after_exertion, repeat_reading_normal, seen_at_clinic_recently, side_effect, new_symptom, caregiver_present, none. Checks, same discipline as the numbers: codes only from the list, the quote must appear in the source text, else the message goes to manual entry. Codes and quotes are stored on the encounter (context_codes_json) and in llm_log.
2. The rules compute the flags and the rule default exactly as in Design A.
3. Julia-1 makes the final decision from facts only. Its state is short, structured and English: the flags with reasons, the rule default, medicine taken at the last three contacts, days silent, and the context codes with their quotes. No raw message text, no Swahili. The question is a three-option typed decision: keep the rule default, one step up, one step down. The criteria are short lists of codes, in config.step_criteria: step up for ran_out_of_pills, missed_doses, cannot_read, lives_alone, unreachable, side_effect, new_symptom; step down for measured_after_exertion with repeat_reading_normal, or seen_at_clinic_recently; keep when no code applies. The guardrail from Design A stays: one step, never down from refer_clinic or emergency_now, emergencies bypass the model, not_sure when the top probability is under min_confidence or the top two are closer than margin. The output names the code and the quote that moved the decision.

A fourth component runs in the evaluation only: a lookup table that applies step_criteria directly to the codes without Julia-1. It is the honest comparison. If the table does as well as Julia-1 on the physician sample, the table becomes the decider and Julia-1 keeps the quiz replies; if Julia-1 does better, it stays as the final step, and the reason it exists is that the physician edits the policy in plain-language criteria without touching code, and it handles combinations the table does not list.

Config additions on the branch: "context_codes" (the vocabulary with a one-line description per code), "step_criteria" (the three lists), and "julia.decide_mode": "actions" for Design A or "facts" for Design B. The switch is the fast revert: after a merge, setting decide_mode back to "actions" restores Design A without touching git.

Branch rules. All Design B code lives on layer3-facts. Main is never edited for B. Commit on the branch after each step. Nobody records the video from the branch until the gate passes and the branch is merged. The time box is half a day for person 2; when the box ends, the gate decides.

The gate to merge, measured by engine/evaluate.py on the branch, against the same labels main uses:
- Context codes: on the synthetic text-modifier cases, which now carry gold codes, code precision and recall both at or above 0.9, and zero accepted codes whose quote is not in the text.
- Plain rows: agreement with the rule default at or above 0.95, not_sure at or below 0.05.
- Text-modifier rows: codes plus Julia-1 at or above codes plus table, and both above rules only.
- Emergencies caught: not fewer than main.
- Physician sample: when filled, agreement on text-modifier rows not lower than main.
If all pass with Julia-1 ahead: merge, decide_mode "facts". If all pass except that the table ties or beats Julia-1: merge with the table as the decider and julia.decide false. If plain rows or emergencies fail: stay on main, keep the branch, write the numbers in the README as a tried alternative.

Revert. Before the merge: `git checkout main`, nothing else. After the merge: set julia.decide_mode to "actions" in the config, restart the server. Both paths are tested once on the branch before any merge.

Prompt for Claude Code (run from a clean main; it creates the branch):
"Read CLAUDE.md and PLAN.md section 5b. Commit any uncommitted work on main first. Then `git checkout -b layer3-facts`. On the branch only: (1) add context_codes, step_criteria and julia.decide_mode to config/guideline_htn.json as section 5b describes, with decide_mode set to facts on this branch; (2) in engine/llm.py extend the extraction prompt and schema so every call also returns context_codes as a list of {code, quote}, check that each code is in config.context_codes and each quote appears verbatim in the source text, store them in encounters.context_codes_json and in llm_log, and send the message to manual entry when a check fails; (3) in engine/decide.py add build_state_facts, which puts flags, rule default, medicine, days silent and the context codes with quotes into a short English state with no raw message text, and a three-option question keep, step_up, step_down with the criteria from config.step_criteria; map the answer to an action on the ladder with the existing guardrail; keep the Design A path untouched and select by config.julia.decide_mode; add a table_decider that applies step_criteria to the codes directly; (4) in data/make_synthetic.py and data/make_labels.py give every text-modifier case its gold context codes and quotes; (5) in engine/evaluate.py report code precision and recall with the quote check, and the decider four ways: rules only, codes plus table, codes plus Julia-1, and Design A, on text-modifier rows and plain rows, with emergencies caught, and save docs/evaluation_layer3_facts.md; (6) tests for the extraction checks, the three-option mapping, the guardrail, and the switch between decide_mode values. Run pytest and evaluate.py and report the gate in section 5b line by line. Do not merge."

Layer 4, the local LLM, is part of the core, not an add-on. It runs as long as the hardware allows: at start, engine/llm.py checks free RAM and picks the primary model or the 1B fallback. It does two jobs only. Job 1, extract: turn one SMS or note into JSON fields (sbp, dbp, meds_taken, symptom codes from the allowed list, free_text_rest), plus a medications variant for the doctor's recommendation. Job 2, summary: write a three-line French "what changed since last contact" from structured fields only. Every output passes four checks before it touches the record: valid JSON, fields match the schema, every number appears in the source text, symptom codes and drug names come from the allowed lists. A failed check sends the message back for manual entry. Every call is written to llm_log. The LLM never chooses an action, never writes advice, and never writes text that goes to a patient.

Fallback order for the clinical cut-offs: Burundi Ministry of Health protocol in French if we find one; then PASCAR (Dzudie et al., 2017); then ISH 2020 (Unger et al., 2020); then the physician's named fallback.

## 6. Build order, owners, and the prompt to paste into Claude Code

Each step: who, the prompt, and "done when". Paste the prompt as written. Claude Code reads CLAUDE.md first. Every step ends with a function in engine/api.py that returns plain JSON, so the frontend can call it.

### Day 1, Saturday

Step 1. Skeleton. Jake, 30 minutes.
Prompt: "Read CLAUDE.md and PLAN.md. Create requirements.txt, .gitignore, the folder layout in PLAN.md section 3 with empty __init__.py files, data/schema.sql with the tables in section 4, and data/make_db.py that takes --db and --schema and creates the database. No model code yet."
Done when: `python data/make_db.py --db data/chw.db --schema data/schema.sql` runs and prints the table names.

Step 2. Synthetic records. Person 1.
Prompt: "Write data/make_synthetic.py with arguments --db, --patients 60, --months 6, --seed 1. Fill the patients and encounters tables exactly as PLAN.md section 4 lists them, including the existing fields: sex, age, number of children, complaint text, tests performed, positive results, referral, doctor's recommendation text with real drug names from config.medications, doctor's notes, bp_text like '140/90', blood sugar with a unit. Give every patient smoking status, height and weight in realistic ranges. Make hypertension patients across 6 villages: some with blood pressure drifting up slowly, some stable, some with missed medication, some smokers with high BMI, some with an emergency symptom, some with missed clinic appointments, and some who never send SMS. Add about 12 text-modifier patients whose right action depends on a note or a message, not on the numbers: a high reading with a CHW note that it was measured after exertion and a normal repeat (one step down); a reading under grade 2 with a message that the pills ran out ten days ago (one step up); a patient travelling for two weeks (SMS recheck instead of a visit); a patient who lives alone and cannot read (visit instead of SMS recheck); chest pain described in an SMS whose symptom extraction was left empty on purpose (emergency); a side effect such as swollen feet in a note (refer). Store each case's intended direction in synthetic_truth as text_modifier (up, down, none) with the reason. Add clinic encounters every 2 to 3 months, CHW encounters every 2 to 4 weeks with CHW notes, Swahili SMS messages from content/i18n/sw_examples.json, follow-ups and appointments (some missed), and time_log rows with realistic visit and travel minutes per village. Keep each patient's hidden trajectory (drifting, stable, deteriorating within 30 days) in a side table named synthetic_truth, for evaluation only. For about eight of the deteriorating patients, add a row in the events table (stroke or hospital admission) at the end of their series, with the weeks before it showing the drift, so the trajectory model has local cases to learn from. Write synthetic = true into the meta table. Print a short summary at the end."
Done when: the database has 60 patients and the summary shows drifting, stable, missed-appointment and silent groups.

Step 2b. Labeled synthetic sets and evaluation. Person 1 with the physician.
Why: Julia-1 and the LLM need no training, but they need a test. The small text model and the 30-day deterioration model need labeled rows. The judges score "evidence it works" (15%), so every model gets a number.
Prompt: "Write data/make_labels.py with --db, --out data/labels, --seed. Make three labeled sets. (1) sms_labeled.csv: 300 synthetic SMS in Swahili, French and English built from templates with random numbers, doses and symptoms, so the gold intent (bp_report, symptom, meds_question, other) and the gold fields (sbp, dbp, meds_taken, symptoms) are known by construction; include 60 messy ones ('150 and 95', typos, mixed language) and 30 with no numbers. (2) decisions_labeled.csv: for every synthetic encounter, the full state string (flags, rule default, adherence, days silent, and the verbatim notes and messages), the rule_default action, the text_modifier from synthetic_truth, the gold action (the rule default moved one step in the modifier's direction, or the rule default when there is none), and the 30-day deterioration label. (3) physician_sample.csv: 50 rows drawn from (2), at least 30 of them text-modifier cases, with an empty 'physician_action' column for the physician to fill in by hand without seeing the suggestion. Then write engine/evaluate.py with --db, --labels, --config that: runs TF-IDF and Julia-1 intent on (1) with a leave-template-out split (train on some message templates, test on templates never seen) and reports accuracy per language for each; runs llm.extract on (1) and reports field accuracy and the rejection rate on messy messages; runs the decider two ways on (2), rules only and rules plus Julia-1 behind the guardrail, and reports agreement with gold and with the physician column, separately for text-modifier rows and plain rows; and runs retrain.py on (2) with a held-out split and reports its scores. Also report lead time, the headline number for the hackathon focus: for every synthetic patient whose readings cross 140/90 or 160/100 during the six months, the number of days between the first trend or borderline flag and the day the threshold was crossed; report the share of such patients flagged before the crossing, the median lead time in weeks, and the same two numbers for a threshold-only configuration, so the gain from catching the trend is visible. Print one table and save it as docs/evaluation.md."
Done when: evaluate.py prints the table and docs/evaluation.md exists. The number that matters for Julia-1 is the gain over rules-only on text-modifier rows against the physician column. Be honest in the README: plain-row labels made from the config rules test that the pipeline follows the rules; the physician sample is the only clinical check; none of this is a clinical validation.

Step 3. Signals. Person 2.
Prompt: "Write engine/signals.py. Read config/guideline_htn.json. For one patient, compute the baseline (mean of the first N readings), the change from baseline, and the slope over the last N readings. Return a list of flags of type threshold, trend, borderline, or symptom. Each flag has reason_text, guideline_ref and emergency yes or no (yes for the severe threshold and the emergency symptoms). Arguments: --db, --config, --patient. Also write tests/test_signals.py with one drifting, one borderline, one stable and one emergency patient."
The borderline flag (added 4 October, the hackathon focus): a patient is borderline when either of two things is true over the last config.borderline.window_readings readings: at least consecutive_readings readings sit within band_below_goal_mmhg under the goal (130 to 139 systolic or 85 to 89 diastolic for a 140/90 goal), or the readings crossed the goal line at least crossings_in_window times (above, then below, then above). The reason_text says which, with the numbers. The action is recheck_7_days, and when ask_weekly_sms is true the follow-up asks the patient for a weekly BP text until two readings in a row are clear of the band. Borderline adds risk points like a trend flag (add "borderline_flag": 2 to config.risk.points). A borderline flag on its own never raises an emergency and never bypasses the model.
Done when: a drifting patient prints flags, a borderline patient prints the borderline flag with the band or the crossings in its reason, a stable patient prints none, the emergency patient prints a flag with emergency = yes, and the tests pass.

Step 4. Decider. Person 2.
First install Julia-1 (from its model card), in the terminal with .venv active:
`python -m pip install huggingface_hub`
`python -c "from huggingface_hub import snapshot_download; snapshot_download('SupersonicLabs/Julia-1', local_dir='models/Julia-1')"`
`python -m pip install -e ./models/Julia-1`
Prompt: "Write engine/decide.py. Step 1, rules: if any flag has emergency = yes, return emergency_now at once with the flag's reason, without calling the model. Otherwise compute rule_default from the flags: the highest action named by any threshold, trend or symptom rule, else routine. Step 2, state: build the state string in this order: each flag's type and reason_text; 'Rule default: <action>'; adherence (meds_taken from the last three contacts) and days since last contact; then 'CHW notes:' with the chw_notes_text of the last two encounters verbatim; then 'Patient messages:' with the last three incoming message texts verbatim, in their original language. Never put bare readings in the state without their flag. Step 3, Julia-1: ask 'Given the rule default and the notes, what should the CHW do next?' with the options from config.actions and the criteria from config.action_criteria, and get the probabilities. Step 4, guardrail: if the chosen option is more than config.julia.decide_max_steps from rule_default on the ladder routine, recheck_7_days, visit_this_week, refer_clinic, clamp it to one step; never move down from an option in config.julia.never_step_down_from; if the top probability is below min_confidence, or the top two are closer than margin, return not_sure. Step 5, output: the final action, rule_default, whether Julia-1 moved it and in which direction, the probabilities, a reason that quotes the sentence in the notes or messages that mattered most when it moved, model_version and config_version. If config.julia.decide is false, return rule_default with reason 'rules only'. Arguments: --db, --config, --patient. Keep the model loaded between calls. Write tests/test_decide.py with: emergency bypass; rules only when decide is false; a step down on the exertion note; a step up on the ran-out-of-pills message; a clamp when the model tries to jump two steps; no step down from refer_clinic."
Done when: the six tests pass, and on the text-modifier patients from step 2 the rules-plus-Julia-1 action matches the intended direction more often than rules alone.

Step 5. Integration contract: engine/api.py and engine/server.py. Person 3, with whoever built the React frontend.
The React app calls the engine over HTTP on the same laptop. engine/api.py holds the functions; engine/server.py wraps them with FastAPI, one route per function, GET for reads and POST for writes, on http://127.0.0.1:8000. FastAPI writes the live contract by itself at http://127.0.0.1:8000/docs, so the frontend team can read every endpoint and try it in the browser. Each function takes plain arguments and returns plain JSON (dicts and lists), never a model object. All text the frontend shows to a patient comes from content/i18n in the patient's language; screen strings are the React app's own.
Prompt: "Write engine/api.py with these functions, each returning JSON-serializable data and each with a one-line docstring: status() returns config_version, approved, model versions, llm model in use, and whether Ollama is reachable; list_patients(filter_text, village, tier) returns one row per patient with id, age, condition_codes, latest sbp and dbp with date, the last six BP readings, next and missed appointments, tier and score, and open flag count; escalation_list() returns patients with an emergency flag and the emergency_protocol text; patient_detail(patient_id) returns the encounters, the BP series, flags with reasons, the current suggestion with probabilities and reason, medications, understanding by topic, follow-ups, and the French summary; inbox(status) returns messages with their extracted fields and valid flag; simulate_incoming(patient_id, text, lang) inserts an incoming SMS row for the demo, since there is no real gateway; process_message(message_id) runs sms.py and returns the fields; confirm_fields(message_id, fields) writes the encounter row; decide(patient_id) and confirm_decision(patient_id, final_choice, override_reason); risk_all(date); plan_week(chw_id, week_start, hours) and update_plan(plan_id, action) and approve_plan(chw_id, week_start); outgoing_queue() and mark_sent(message_id); next_question(patient_id, channel) and record_answer(patient_id, question_id, reply_text); hours(chw_id, week_start) and log_time(chw_id, activity, patient_id, start, end); metrics(month); phone_thread(patient_id) returns every incoming and outgoing SMS for one patient in order, for the virtual phone; demo_script() returns the ordered demo messages from docs/demo_script.json; run_review() runs retrain.py, writes the new model version, and returns its scores (a supervisor action, pressed by a person); feedback(who, target, value, note) stores a thumbs up or down. Functions for steps not yet built return {"error": "not built yet", "step": N} instead of raising. Then write engine/server.py with FastAPI: one route per function, GET for reads (for example GET /patients, GET /patients/{id}, GET /escalation, GET /inbox, GET /plan?chw=1&week_start=2026-10-05&hours=20, GET /metrics?month=2026-09, GET /status) and POST for writes with a JSON body (for example POST /messages/incoming, POST /messages/{id}/process, POST /messages/{id}/confirm, POST /patients/{id}/decide, POST /patients/{id}/confirm_decision, POST /plan/{id}/update, POST /plan/approve, POST /messages/{id}/sent, POST /quiz/answer, POST /time). Bind to 127.0.0.1 port 8000 only. Allow CORS from http://localhost:3000 and http://localhost:5173, which is where the React dev server runs. Add a /health route that returns ok. Write docs/integration.md that lists every route, its arguments, and an example of its JSON output, and tells the frontend team to open /docs."
Start it with: `uvicorn engine.server:app --host 127.0.0.1 --port 8000`. The React app starts in frontend/ with its own `npm start` or `npm run dev`. Both are local, so the demo runs with WiFi off. For the final demo, `npm run build` and serve the built files from the same FastAPI app under /app, so one command starts everything; do this only after everything else works.
Done when: the React app lists patients and opens one patient from the synthetic database through the API, /docs shows every route, and docs/integration.md matches the code.

### Day 2, Sunday

Step 6. Layer 4, SMS parsing and record import. Person 2. Do this first on Sunday.
Prompt for engine/llm.py: "Write engine/llm.py that talks to Ollama on localhost. At start, check free RAM; if it is below config.llm.min_free_ram_gb use config.llm.fallback_model, else config.llm.model. Function extract(text) sends a fixed prompt asking for JSON only with the fields sbp, dbp, meds_taken, symptoms, free_text_rest, with temperature 0 and format json. It is used for SMS, complaint text, doctor's notes and CHW notes. Then check: valid JSON, schema, every number in the JSON appears in the source text, symptoms only from config.llm.symptom_codes. Return the fields and valid true or false with a reason. Function medications(text) does the same for a doctor's recommendation: a list of name and dose_text, every name must appear in the source text and in config.medications. Function summary(patient_id) builds a three-line French note from the last two encounters and the open flags, from structured fields only. Write every call to llm_log. Keep the model loaded with keep_alive. Arguments for a command line test: --text or --patient."
Prompt for engine/sms.py: "Write engine/sms.py. For one message: regex for numbers like 140/90, Julia-1 for the intent (bp_report, symptom, meds_question, other), then llm.extract for the fields. If the regex and the LLM disagree on a number, mark the message for manual entry. Write parsed_json to messages, and an encounter row only when confirm_fields is called. Arguments: --db, --message-id. Messages wait in status queued until mark_sent is called. Wire process_message, confirm_fields and inbox in engine/api.py."
Prompt for data/import_records.py: "Write data/import_records.py with --db and --csv. Read a CSV whose columns are the existing record fields in PLAN.md section 4, split bp_text into sbp and dbp, map positive results to condition codes, and insert patients and encounters. Print counts and any rows it could not read."
Done when: a clean SMS fills the fields with no edits, a messy SMS with "150 and 95" fills them too, a message with an invented number is rejected, the summary reads correctly to the French speaker, and a sample CSV imports.

Step 7. Follow-ups, reminders and hours. Person 3.
Prompt: "Write engine/followups.py and engine/hours.py. followups.py: list due, missed and done follow-ups including clinic appointments; create an outgoing SMS from content/i18n/sms_templates.json config.reminders.days_before_appointment days before an appointment, and a follow-up message config.reminders.follow_up_after_visit_days days after a visit, both in the patient's language, queued until mark_sent. hours.py: log_time writes to time_log with the patient's village; hours(chw_id, week_start) returns the weekly table per activity and the average visit and travel minutes per village; a --csv argument writes the table to a file. Wire outgoing_queue, mark_sent, hours and log_time in engine/api.py."
Done when: reminders appear in the outgoing queue and the weekly hours table returns from api.hours.

Step 8. Risk score. Person 2 with the physician.
Prompt: "Write engine/risk.py. For each patient: read the base 10-year CVD risk from the lookup table named in config.risk.base_table using age, sex, smoking, latest systolic BP and BMI; if the table file is missing, use 0 and print a warning. Add the points in config.risk.points for each active flag, missed medication, more than 30 days of silence, an overdue follow-up, a missed appointment, and low quiz understanding. Set the tier from config.risk.tiers. Write score, tier, components_json and config_version to risk_scores. Arguments: --db, --config, --date. Print the top 10 with their components. Write tests/test_risk.py. Wire risk_all in engine/api.py."
Done when: a patient with a symptom flag ranks above a stable patient, and a silent patient's score rises over time.

Step 9. Weekly planner. Person 2 and person 3.
Prompt: "Write engine/plan_week.py. Exclude patients in the escalation list; they are handled by the protocol, not the plan. Read the latest risk_scores, config.capacity, and the average visit and travel minutes per village from time_log, with the config defaults as fallback. Rank patients by score per estimated minute. Assign visits in rank order until the hours run out, grouping visits in the same village together to save travel; then SMS check-ins for the next patients; the rest wait. Give each row a one-line reason_text. Write rows to plans. Arguments: --db, --config, --chw, --week-start, --hours. Wire plan_week, update_plan and approve_plan in engine/api.py so the frontend can move a patient between visit, SMS and wait, show hours used and left, and approve. A person approves every plan."
Done when: changing --hours changes the number of visits, villages are grouped, emergencies never appear in the plan, and edits made through update_plan are stored.

Step 10. Patient education questions. Person 4 with the physician and the bilingual teammate.
First, people write the question bank: content/quiz/patient_htn.json, about 15 questions across the five topics in config.quiz.topics, each with three options, the correct option, a one-line correction text, a source, and the text in Swahili, French and English (Kirundi added in step 12, marked unverified). The LLM does not write any of it.
Prompt: "Write engine/quiz.py. Function next_question(patient_id, channel) picks the question whose next_due is earliest, rotating topics, and returns it in the patient's language with 'Reply 1, 2 or 3', and queues it as an outgoing SMS. Function record_answer(patient_id, question_id, reply_text) maps a reply of 1, 2 or 3 to the option; if the reply is other text, ask Julia-1 which option it matches, or 'unclear'. Store the row in quiz_results, set next_due from config.quiz (7 days after a wrong answer, 30 after a right one), and on a wrong answer queue the correction text as an outgoing SMS for the CHW to send. Function understanding(patient_id) returns the share correct per topic over config.quiz.window_days. Function support_needed() returns patients and topics below config.quiz.low_understanding_below, for a chart in the frontend. Hook: after process_message confirms a BP report, and after a CHW visit is logged, call next_question. risk.py adds config.risk.points.low_understanding when danger_signs or medication is below the cut-off. Wire next_question, record_answer and support_needed in engine/api.py."
Done when: a patient answers two questions by SMS, understanding returns the scores, a wrong answer brings the question back in 7 days, and a patient with low danger-sign understanding moves up the weekly plan.

Step 11. Efficacy metrics and learning loop. Person 2 and person 4.
Prompt: "Write engine/metrics.py with metrics(month) returning, by month: suggestions accepted, override rate split into up and down, not_sure rate, days from flag to contact, overdue and missed counts, hours per week, planned visits completed, low-yield visits, share of visits that went to high-tier patients, mean patient understanding by topic, share of patients who know the danger signs; patients at BP goal, mean change from baseline, referrals completed, events, events with a prior flag, confirmed false positives and false negatives from error_reviews (step 11c). Wire it in engine/api.py. Write engine/retrain.py with --db, --labels and --out that trains TF-IDF plus logistic regression on the decisions table (or on data/labels/decisions_labeled.csv when the table is small) for the next action, and a second logistic regression that predicts deterioration within 30 days from the risk components. Print held-out scores. A person runs it; it never runs by itself."

Step 11c. Catching false positives and false negatives. Person 2 with the physician.
What it is: the tool watches its own mistakes. A false positive is a flag or suggestion that sent a CHW to a patient who did not need it. A false negative is a patient who got worse without a flag, or with a suggestion that was too low. Neither can be seen at the moment of the decision; both become visible later, from what the CHW did and what happened to the patient. So the engine collects every signal of a miss or an over-call into one table, error_reviews, and the supervisor labels them at the monthly review. Nothing changes a threshold or a model by itself; the review queue is the input to steps 11 and 11b.
Where the signals come from, and what each one means:
- Override down (CHW chose a lower action than the suggestion): a candidate false positive against the rule or model that made the suggestion. Override up: a candidate false negative.
- Event without a flag: any row in the events table with no flag of any type in the 90 days before it is a false negative, with a cause to find. The engine pre-fills the cause: silence (no readings in the window), rule (readings present but no rule fired), extraction (a symptom appears in the raw text but not in symptom_codes), model (Julia-1 stepped down from the rule default), or unknown.
- Referral outcome: a clinic referral whose outcome is "sent home, no change" is a candidate false positive; "admitted" or "treatment changed" confirms the call. The outcome comes from followups and the referral field.
- Field corrected: when the CHW edits an extracted field before confirming, the original and the corrected values are stored. A changed number or symptom is an extraction error, counted per model version.
- Trajectory miss: a trajectory flag with no event in the horizon is a candidate false positive; an event in a patient the trajectory model scored low is a false negative. Counted per model version.
- Visit found nothing: a visit_this_week whose visit recorded BP at goal, medication taken and no symptoms is logged as a low-yield visit, not an error, because the hours it cost are the price of sensitivity. It is shown as a cost next to the false-negative count, so the physician can see the trade-off when moving a threshold.
Prompt: "Write engine/errors.py. Function detect(as_of_date) scans decisions, flags, events, followups, messages and trajectory_scores and inserts one error_reviews row per new signal listed in PLAN.md step 11c, with evidence_json holding the linked rows and the pre-filled cause_code, and supervisor_label pending. It never inserts the same linked_id twice. Function label(review_id, supervisor_label, cause_code, reviewed_by) updates a row. Function summary(month) returns, per rule_id and per model_version: flags raised, confirmed false positives, confirmed false negatives, pending reviews, low-yield visits, and the derived rates: share of events with a prior flag (sensitivity proxy) and share of flags followed by a confirmed action (precision proxy). Function replay(config_candidate) re-runs signals.py over the stored encounters with a changed config and reports how many flags would have fired and how many of the recorded events would still have had a prior flag, so a threshold change is seen before it is made. Arguments: --db, --config, --as-of. Wire detect, label, summary and replay in engine/api.py and engine/server.py (POST /errors/detect, POST /errors/{id}/label, GET /errors/summary?month=, POST /errors/replay). run_review calls detect first and returns the summary with the retrain scores. Write tests/test_errors.py: an event with no prior flag becomes a false_negative with cause silence; an override down becomes a false_positive; a corrected field becomes an extraction_error; replay with a higher trend threshold reports fewer flags and names which events lose their prior flag."
Also in engine/evaluate.py: report confusion matrices, not only accuracy, for the rules per rule_id, the decider, the extraction step and the trajectory model, with false negatives listed separately from false positives, because a missed stroke and an unneeded visit are not the same mistake.
Done when: the synthetic database produces a review queue with all three kinds, the supervisor page shows the per-rule table and the sensitivity and precision proxies by month, and replay runs in under ten seconds.
For the README and the video, one sentence: every missed event and every override becomes a review card that a person labels, and the thresholds move only after a replay shows what the change would have caught and what it would have cost.

Step 11b. Peer trajectories: learning from local patients who progressed to an event. Person 2 with the physician.
What it is: the patient's recent pattern is compared with the patterns of local patients who went on to a stroke or an admission. The output is a flag with a plain reason ("7 of the 10 most similar local patients had an event within 90 days") and the three closest peer cases shown as anonymised BP charts beside the patient's own. It is grounded in the same catchment, which is the brief's "localizing AI" question answered directly.
Prompt: "Write engine/trajectory.py. For every patient and every past date, build a window from the last config.trajectory.window_readings readings: systolic BP values resampled to that length, slope, standard deviation, adherence rate, symptom count, days silent, quiz understanding. Standardise the features. Label a historical window positive when the events table holds an event for that patient within config.trajectory.horizon_days after the window's end, else negative; use only data from before the event. Fit a distance-weighted k-nearest-neighbours model (scikit-learn, k from config) on the historical windows. For the current window of each patient, return the share of the k nearest peers that were positive, the ids of config.trajectory.show_peers closest peers, and a reason_text in plain words. Set flag yes when the share is at or above config.trajectory.flag_share_gte. If the events table holds fewer than config.trajectory.min_events rows, return share null, flag no, and reason 'not enough local events yet', so the rules carry the load. Write trajectory_scores. risk.py adds config.trajectory.points when flag is yes, and decide.py puts the reason into the state string. Arguments: --db, --config, --date. Also write the evaluation in engine/evaluate.py: leave one patient out at a time, score only windows that end before any event, report AUC and the share flagged among patients with and without an event. Wire trajectory(patient_id) in engine/api.py, returning the share, the reason, and the peer BP series by anonymised id, and GET /patients/{id}/trajectory in engine/server.py."
Honest limits, for the README and the video: with 60 synthetic patients the events are few, so this shows the mechanism, not a validated predictor. In deployment the feature stays off until the local events table reaches min_events. A small local positive set can encode local habits about who gets seen, so the physician reviews the flagged peer cases at the monthly review (step 11), and peers appear by anonymised id only, to the CHW only.
Done when: a synthetic patient whose series matches the pre-event weeks of the event patients gets the flag with a reason and three peer charts, a stable patient does not, and the leave-one-out table prints.

Step 12. Languages. Person 4 with the bilingual teammate.
Finish sw.json, fr.json, the quiz bank and sms_templates.json. The teammate checks every Swahili SMS template, every quiz question and correction, and every French summary template. Make rn.json (Kirundi) with NLLB-200 from the French file, mark it "verified": false so the frontend can show "not yet verified", and list it as machine-translated in docs/sources.md. Screen strings in English, French, Swahili and Kirundi are the frontend's; hand the same four language codes and the verified flags to the frontend team.

Step 13. Lovable demo: the virtual phone and the scripted scenario. Person 3 (engine routes) and the frontend team (the phone panel).
What it is: a phone-shaped panel in the React app, beside the CHW screens. It shows one patient's SMS thread. The presenter types or taps a preset message as the patient, and the audience watches the engine read it, the CHW confirm it, the flag appear, the plan change, the quiz question arrive on the phone, and the monthly review produce a new model version. One take, about three minutes.
Engine side. Prompt: "Write docs/demo_script.json: an ordered list of steps for one fictional patient (invented name, 58, village Kirundo), each with the patient's message in Swahili (placeholder text; the teammate writes the real Swahili), its English gloss, and the expected engine result. Wire phone_thread, demo_script, run_review and feedback in engine/api.py and engine/server.py (GET /phone/{patient_id}, GET /demo/script, POST /review/run, POST /feedback). run_review calls retrain.py on the decisions table, stores the new model_version, and returns the held-out scores and the number of overrides and quiz answers it learned from."
Frontend side (the React team): a phone panel that polls GET /phone/{patient_id} every two seconds, a text box and preset chips from GET /demo/script, incoming bubbles on the left and outgoing on the right, a "Sync" button that calls POST /messages/{id}/sent for queued outgoing messages so the audience sees the queue empty when "the signal comes back".
The scenario, in order:
1. WiFi off on screen. English interface. Open the dashboard.
2. Phone: the patient texts a BP report in Swahili with a half dose and a headache. Engine: Julia-1 says bp_report, the LLM extracts 150/95, meds partial, headache; the inbox shows text and fields side by side; the CHW taps Confirm.
3. Flag: trend, up from baseline over five weeks, with the reason and the guideline reference, and the patient's own chart showing the readings still under 140/90 while the line climbs. Say out loud: no threshold has been crossed yet; this is the patient the tool is for. Risk tier moves to high.
4. Suggested action: visit this week, with its probability. The CHW overrides to recheck in 7 days with the reason "patient travelling". The override is stored. Say out loud: this is feedback into the model.
5. Phone: the quiz question arrives (danger signs). The patient replies 2, which is wrong. The correction is queued; the CHW taps Send; it appears on the phone. The understanding score drops; risk points are added.
6. Weekly plan: the patient now appears in this week's list; the CHW moves one patient and approves the plan.
7. Supervisor page: "Run monthly review". The review queue shows the override from step 4 as a candidate false positive and one synthetic patient whose event had no prior flag as a false negative with its cause; the supervisor labels both. Replay shows what a lower trend threshold would have caught and what it would have cost in visits. Then the engine retrains on the decisions table, including the override and the quiz answer, shows the new model version and its held-out score, and the next suggestion for any patient carries the new version. Say out loud: a person pressed this; the model never updates itself.
8. Switch the language to French, then Swahili.
Done when: the eight steps run without a restart on the synthetic database, twice in a row.

Step 14. README, sources, video. Jake and person 4.
README sections: problem statement; what the AI does and why a spreadsheet cannot; how the weekly plan ranks, and that the ranking itself is arithmetic fed by the AI signals; guardrails (fixed answer list, not_sure, emergency escalation outside the plan, human confirms every field, action and plan, the approved flag, no diagnosis); evaluation table from docs/evaluation.md with its limits; data sources and what they do not cover (synthetic); privacy (where data sits, who can read it, lost device); languages, with the Kirundi answer; tech stack and the integration contract; installation and startup instructions for the engine and the frontend; the eight-step demonstration workflow from docs/build_instructions.md.
Video, 2 to 5 minutes, the five parts from the brief, page 10, built around the step 13 scenario. Show WiFi off at the start. Show the weekly plan change when the hours change. Show the language switch.

Step 15. Optional, only after the video is recorded: one-click local program.
Why: the models already use the laptop's hardware. Ollama runs natively and uses the GPU where there is one (Metal on a Mac), and Julia-1 runs in the Python process. The browser only draws the screens, so an exe makes nothing faster. What an exe adds: no terminal, no address bar, one icon to start, and a folder on a USB stick that installs offline, which is a strong "side-load" story for the judges.
Simplest path, in two stages. Stage 1: `npm run build` the React app, serve the built files from engine/server.py under /app, and package the engine with PyInstaller into one folder (`pyinstaller --onedir`) whose start script launches the server and opens http://127.0.0.1:8000/app in the default browser. Ollama is still installed separately; the start script checks for it and says what to do if it is missing. Stage 2, later: replace the Ollama call in engine/llm.py with llama-cpp-python loading the same GGUF file in-process, so the folder needs no separate install at all. Do not start stage 2 this weekend.
Prompt for stage 1: "Add a route in engine/server.py that serves the files in frontend/build under /app. Write run_twese.py that starts uvicorn on 127.0.0.1:8000, waits for /health, and opens http://127.0.0.1:8000/app in the browser; if Ollama is not reachable, print the install link and keep running. Write build_exe.sh that runs PyInstaller in onedir mode on run_twese.py, including config/, content/ and the Julia-1 model folder as data, and prints the output folder size."
Done when: a teammate who has never opened a terminal starts the program by double-clicking, with WiFi off.

## 7. The floor

If time runs out: steps 1 to 6 wired into the React app through engine/server.py, the virtual phone with at least the message box and the thread (the first half of step 13), the README, and the video are the entry. Bonuses in this order: 7, 8, 9, 2b, 10, the rest of 13, 12, 11, 11c, 11b, and 15 last. If only one of 11b and 11c fits, build 11c: the judges' pass/fail is about safety, and catching misses is the safety story. Steps 7 to 9 together are the "amplify the CHW" story, so protect them. The quiz bank (step 10) and the physician sample (step 2b) can be written by the physician in parallel from Saturday, since they need no code.

Where the remaining hours go, given the 4 October focus on borderline and trending patients, in this order: the borderline flag and the lead-time metric (step 3 and 2b); the social-code recall fix in the extraction prompt, because missed doses and running out of pills are what pushes a borderline patient over; the number checks in step 6; the branch gate and merge for the decider; then the demo scenario, which is already a drifting patient. The emergency keyword net for danger words in raw text is in the later list below.

Later, after the hackathon: the emergency keyword net for raw text; the one-click program (step 15); distilling the extraction to a small encoder for the tablet, trained on confirmed extractions and CHW corrections; fine-tuning for extraction off the laptop once months of corrections exist.

## 8. Rules checklist (brief, pages 7 and 11)

- Runs on a device the user has: laptop now; the tablet path is explained.
- Core works offline: yes; show it. Records, measurements, CHW activities and outgoing SMS are stored and queued offline.
- Model files small enough to side-load: Julia-1 is 550 MB; the LLM is about 2.5 GB and moves by USB, which we show.
- One interaction in a local language: Swahili SMS. Name it. Prepare the Kirundi answer: NLLB-200 (run_Latn) into French, then the same pipeline; Kirundi text exists but is marked unverified.
- Human in the loop: confirm or override each action; approve each weekly plan; confirm every extracted field; a CHW taps Send on every quiz correction; not_sure fallback; a person approves every model update.
- Misses are caught: every override, every event without a prior flag, every referral sent home and every corrected field becomes a review card; the supervisor labels it; thresholds move only after a replay shows the effect on past events.
- Emergencies follow the approved protocol, outside the planner.
- Clinical thresholds: the approved flag and banner. Placeholders are never presented as approved guidance.
- Patient-facing text: quiz questions, corrections, reminders and follow-ups are written by people and cited. The LLM never writes text that goes to a patient.
- No diagnosis, no images: flags, reasons, scores and routing only.
- Cite every data source. Say what the synthetic data does not cover.
- Privacy statements: one encrypted SQLite file, PIN, two roles, audit table, useless without the passphrase if lost.

## 9. Risks and fallbacks

- Julia-1 does not install: use TF-IDF plus logistic regression for intent and a rule table for the action. Keep the fixed list and not_sure.
- Swahili accuracy is low: say so; translate to French with NLLB-200 first (about 1 GB more).
- The WHO risk chart table is not ready: points only; say so in the README.
- The LLM is slow or RAM is short: llm.py switches to gemma3:1b by itself. If even that fails on the demo laptop, close other apps; the regex and Julia-1 path still fills the clean fields, and the inbox marks the rest for manual entry.
- RAM budget on 8 GB: OS about 2 GB, Julia-1 0.6 GB, Python and FastAPI 0.5 GB, LLM at 4-bit about 3 GB loaded, the React app in one browser tab about 0.4 GB. Use the built React app, not the dev server, for the demo, and close the browser tabs you do not need.
- Frontend integration: the React app and the engine meet only at the HTTP routes in step 5. Agree the route list before Sunday. If a route is not ready, the frontend shows "not built yet" from the error JSON, not a blank screen. The frontend team can build against the synthetic database as soon as step 5 runs, before the models exist.
- CORS or port errors in the browser: check that uvicorn runs on 127.0.0.1:8000 and that the React app calls that address; /health must return ok in the browser first.
- Merge conflicts: one folder per person; pull before you start.
- The exe (step 15) eats an evening: PyInstaller and a model folder of 3 GB can fail in slow ways. Record the video first, then try it, and stop at a set time.
- A step takes longer than planned: stop at the floor in section 7.

## 10. Sources

- Hack-Nation x World Bank brief, Small AI for Development, pages 7, 10, 11, 13, 14.
- Julia-1 model card, Supersonic Labs: https://huggingface.co/SupersonicLabs/Julia-1
- MASSIVE dataset, Amazon: listed in the brief, page 9.
- NLLB-200 and FLORES-200, Meta: listed in the brief, page 8.
- Unger T, et al. 2020 International Society of Hypertension global hypertension practice guidelines. J Hypertens 2020. https://pmc.ncbi.nlm.nih.gov/articles/PMC8762770
- Dzudie A, et al. Roadmap to achieve 25% hypertension control in Africa by 2025. Cardiovasc J Afr 2017. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5642030/
- WHO CVD Risk Chart Working Group. World Health Organization cardiovascular disease risk charts: revised models to estimate risk in 21 global regions. Lancet Glob Health 2019. https://www.thelancet.com/journals/langlo/article/PIIS2214-109X(19)30318-3/fulltext
- Gemma 3, Google, 2025: https://ollama.com/library/gemma3 (gemma3:4b primary, gemma3:1b fallback)
- Ollama: https://ollama.com
- Claude Code install: https://docs.claude.com/en/docs/claude-code/overview
