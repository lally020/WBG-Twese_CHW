# Integration contract: engine and React frontend

The React app (frontend/) talks to the engine over HTTP on the same laptop. Nothing leaves the machine.

- Start the engine: `uvicorn engine.server:app --host 127.0.0.1 --port 8000`
- Check it: open http://127.0.0.1:8000/health in the browser. It must say `{"status":"ok"}`.
- **Live contract: open http://127.0.0.1:8000/docs.** FastAPI lists every route there, and you can try each one in the browser.
- CORS allows http://localhost:3000 and http://localhost:5173 (and the 127.0.0.1 versions).
- Every route returns JSON. A problem comes back as `{"error": "..."}` with status 200, so show the message rather than a blank screen.
- Show the banner from `GET /status` (`banner`) whenever `approved` is false.
- Screen strings are the frontend's own. Patient-facing SMS text comes from the engine (content/i18n), already in the patient's language. Stored data are codes and never change with the language.
- Keep these three stable for the virtual phone: `GET /phone/{patient_id}`, `POST /messages/incoming`, `POST /messages/{id}/sent` (plus `POST /sync`).

## Routes

| Method | Path | What it does | Arguments |
|---|---|---|---|
| GET | `/health` | Liveness check. Open it in the browser first. | - |
| GET | `/status` | Config version, approved flag and banner text, model versions, LLM in use, Ollama reachable, languages. | - |
| GET | `/languages` | The four language codes with their verified flags. | - |
| GET | `/patients` | A. Dashboard rows, emergencies first, then by score. Active patients only. | filter_text, village, tier, chw (all optional; chw = one CHW's patients) |
| GET | `/patients/{id}` | A/B. One patient: encounters, BP series, baseline, flags with reasons, current suggestion, medications, understanding, follow-ups, risk, trajectory, French summary. | include_summary (default true; the summary calls the LLM, 1 to 3 s) |
| GET | `/patients/{id}/summary` | Only the three-line French summary (summary_fr in the detail). | - |
| GET | `/escalation` | Patients with an emergency flag, with the protocol text from docs/emergency_protocol.md in the requested language (English when the language is not in the file). Never in the plan. | lang (en, fr; default en) |
| GET | `/protocol` | Only the emergency protocol text, in one language. | lang |
| POST | `/messages/incoming` | C. The virtual phone sends a patient SMS. While a quiz question is pending, a bare 1, 2 or 3 is recorded as the answer (routed_to quiz). Any free text goes to the inbox and is processed at once (auto_process); if it also matches a quiz option, the answer is recorded too (routed_to inbox+quiz), so a symptom report is never swallowed by the quiz. | body: patient_id, text, lang (optional), auto_process (default true) |
| GET | `/inbox` | B/C. Incoming messages with extracted fields; state is new, needs_review or confirmed. | status: new, needs_review, confirmed, all |
| POST | `/messages/{id}/process` | Re-run the parse on one message (regex, intent, LLM, four checks). | - |
| POST | `/messages/{id}/confirm` | The CHW confirms (or corrects) the fields. Writes the encounter, refreshes flags, suggestion and risk, and queues the next quiz question after a BP report. | body: fields {sbp, dbp, meds_taken, symptoms} |
| POST | `/patients/{id}/decide` | B. Suggest the next action: rules first (rule_default), then Julia-1 may move it one step when a note or message causes it (moved, direction, moved_by quotes the sentence). Emergencies bypass the model; low or close probabilities give not_sure. | - |
| POST | `/patients/{id}/confirm_decision` | The CHW confirms or overrides. Stored; creates every follow-up in config.followup_after_decision for that action (followups list; urgent referrals use days_if_urgent). | body: final_choice (one of config actions), override_reason |
| GET | `/outgoing` | C. Outgoing SMS waiting for Sync (reminders, follow-ups, quiz, corrections). Also queues due reminders. | - |
| POST | `/sync` | Marks every queued outgoing SMS as sent ('the signal comes back'). | - |
| GET | `/phone/{id}` | Virtual phone: every SMS for one patient, oldest first. Poll every 2 s. | - |
| POST | `/messages/incoming` | (Quiz reply) the same route: a reply of 1, 2 or 3 to a pending question. | - |
| POST | `/messages/{id}/sent` | Mark one outgoing SMS as sent (the CHW taps Send on a correction). | - |
| POST | `/quiz/next` | E. Ask the next education question (queued as SMS for channel sms). | body: patient_id, channel (sms or visit) |
| POST | `/quiz/answer` | E. Record an answer directly (the phone route does this for you). | body: patient_id, question_id, reply_text |
| GET | `/quiz/support` | E. Patients and topics below the understanding cut-off, for a chart. | - |
| POST | `/risk/run` | D. Re-score every patient (optional body: date). | - |
| GET | `/plan` | D. The weekly plan: kept until the hours change; regenerate=true rebuilds it. Rows are ranked; route groups visits by village. | chw, week_start (YYYY-MM-DD, default next Monday), hours (default config), regenerate |
| POST | `/plan/{id}/update` | Move one patient between visit, sms and wait. Re-opens approval. | body: action |
| POST | `/plan/approve` | A person approves the plan. | body: chw_id, week_start |
| GET | `/hours` | D. Weekly hours per activity, capacity, average visit and travel minutes per village. | chw, week_start (default this Monday) |
| POST | `/time` | Log CHW time. A visit closes the CHW follow-up, marks the planned visit done, and returns the question to ask in person. | body: chw_id, activity (visit, sms, travel, admin), patient_id, start, end |
| POST | `/patients/{id}/goal_override` | Supervisor only: set or clear a patient's BP goal override (high_risk uses config.bp_goal_high_risk). The PIN's role is checked before anything is written. Create users with: python engine/api.py add-user --name ... --role supervisor --pin .... | body: goal_override (high_risk or null), pin |
| POST | `/inbox/process` | Process waiting incoming SMS in one batch (the local LLM takes about 1.7 s per message on one laptop). | body: limit (default 100) |
| GET | `/followups` | Due, missed and done follow-ups (clinic appointments included). | patient_id (optional) |
| GET | `/patients/{id}/trajectory` | Peer trajectory: share of similar local patterns followed by an event, reason, anonymised peer BP series. Off until config.trajectory.min_events. | min_events (demo override) |
| GET | `/metrics` | Monthly efficacy metrics. | month YYYY-MM (default this month) |
| POST | `/errors/detect` | Step 11c: look for new suspected misses and over-calls (overrides up or down, events without a prior flag with a pre-filled cause, referrals sent home, corrected SMS fields, trajectory misses both ways). Never adds the same one twice. The monthly review runs this first. | body: as_of (optional) |
| GET | `/errors` | The review cards, newest first. | label (pending, confirmed_error, correct_call, unavoidable) |
| POST | `/errors/{id}/label` | Supervisor only (PIN): label one review card. A confirmed error needs a cause_code. | body: supervisor_label, cause_code (threshold, trend_rule, symptom_rule, extraction, silence, model, chw_judgement, other), pin |
| GET | `/errors/summary` | Per rule and per model version: flags raised, confirmed false positives and false negatives, pending, low-yield visits; sensitivity proxy (events with a prior flag) and precision proxy (flags followed by a confirmed action). | month YYYY-MM (optional; all time when missing) |
| POST | `/errors/replay` | Re-run the rules on every stored contact with a changed config, before making the change: flags now and with the change, extra contacts flagged (the cost), and which past events gain or lose a prior flag. Writes nothing. | body: changes (merged into the current config) |
| POST | `/patients/{id}/referral_outcome` | After a referral or emergency (protocol step 7): seen, admitted, treatment_changed, sent_home (no change) or not_reached. Admitted also adds a hard outcome to events; sent_home becomes a candidate false positive. | body: outcome |
| POST | `/feedback` | Thumbs up or down. | body: who (chw, patient), target (suggestion, plan), value, note |
| POST | `/import` | Import existing records from CSV text (columns in data/import_records.py). | body: csv_text |
| GET | `/demo/script` | The ordered demo steps from docs/demo_script.json, for the preset chips. | - |
| POST | `/review/run` | Monthly review, pressed by a supervisor: retrain, store the new model version, return held-out scores and what it learned from. Takes a few seconds. | - |
| POST | `/demo/reset` | Rebuild the synthetic database (same patients), so the demo can run again. | body: seed (default 1) |

## Example responses

Generated from real calls on the synthetic database (long lists cut to two items).

### GET /health
```json
{
  "status": "ok"
}
```

### GET /status
```json
{
  "name": "TWESE CHW AI",
  "config_version": "2026-10-04a",
  "approved": false,
  "approved_by": "",
  "banner": "Thresholds not yet physician-approved. Demonstration only.",
  "synthetic_data": true,
  "models": {
    "decider": "rules-v1",
    "decider_reviewed_version": null,
    "julia_1": {
      "available": false,
      "error": "Julia-1 failed to load: cannot import name 'load_model' from 'julia' (unknown location)",
      "used_for": [
        "intent (fallback_only)",
        "decide",
        "... 1 more"
      ]
    },
    "trajectory": "knn-v2",
    "llm": "hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M",
    "llm_primary": "hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M",
    "llm_fallback": "gemma3:1b"
  },
  "ollama_reachable": true,
  "free_ram_gb": 5.6,
  "who_risk_table_loaded": true,
  "languages": [
    {
      "lang": "en",
      "name": "English",
      "verified": true,
      "note": "English source text"
    },
    {
      "lang": "fr",
      "name": "Français",
      "verified": false,
      "note": "Drafted by Claude Code on 2026-10-03; awaiting review by the bilingual teammate"
    },
    "... 2 more"
  ],
  "today": "2026-10-03"
}
```

### GET /languages
```json
{
  "languages": [
    {
      "lang": "en",
      "name": "English",
      "verified": true,
      "note": "English source text"
    },
    {
      "lang": "fr",
      "name": "Français",
      "verified": false,
      "note": "Drafted by Claude Code on 2026-10-03; awaiting review by the bilingual teammate"
    },
    "... 2 more"
  ]
}
```

### GET /patients?chw=1
```json
{
  "count": 60,
  "patients": [
    {
      "id": 17,
      "age": 44,
      "sex": "male",
      "village": "Kirundo",
      "language": "sw",
      "condition_codes": [
        "hypertension"
      ],
      "latest_bp": {
        "sbp": 162,
        "dbp": 100,
        "date": "2026-10-01"
      },
      "last_six_bp": [
        {
          "date": "2026-08-28",
          "sbp": 156,
          "dbp": 105
        },
        {
          "date": "2026-08-30",
          "sbp": 145,
          "dbp": 92
        },
        "... 4 more"
      ],
      "latest_blood_sugar": {
        "value": 4.7,
        "unit": "mmol/L",
        "date": "2026-09-05",
        "used": false
      },
      "next_appointment": "2026-11-10",
      "missed_appointments": [
        "2026-07-07"
      ],
      "tier": null,
      "score": null,
      "open_flag_count": 3,
      "emergency": true,
      "last_contact": "2026-10-01"
    },
    {
      "id": 21,
      "age": 50,
      "sex": "female",
      "village": "Rutana",
      "language": "rn",
      "condition_codes": [
        "hypertension"
      ],
      "latest_bp": {
        "sbp": 171,
        "dbp": 113,
        "date": "2026-09-30"
      },
      "last_six_bp": [
        {
          "date": "2026-09-07",
          "sbp": 149,
          "dbp": 95
        },
        {
          "date": "2026-09-08",
          "sbp": 151,
          "dbp": 92
        },
        "... 4 more"
      ],
      "latest_blood_sugar": {
        "value": 6.4,
        "unit": "mmol/L",
        "date": "2026-09-25",
        "used": false
      },
      "next_appointment": "2026-12-15",
      "missed_appointments": [],
      "tier": null,
      "score": null,
      "open_flag_count": 3,
      "emergency": true,
      "last_contact": "2026-09-30"
    },
    "... 58 more"
  ],
  "villages": [
    "Cibitoke",
    "Gitega",
    "... 4 more"
  ]
}
```

### GET /patients/1?include_summary=true
```json
{
  "patient": {
    "id": 1,
    "sex": "female",
    "age": 58,
    "number_of_children": 1,
    "smoker": "no",
    "height_cm": 168.0,
    "weight_kg": 66.3,
    "village": "Kirundo",
    "phone_hash": "d9416d30147e7f61",
    "language": "sw",
    "enrolled_on": "2025-10-28",
    "condition_codes": [
      "hypertension"
    ],
    "chw_id": 1,
    "goal_override": null,
    "status": "active"
  },
  "approved": false,
  "bp_series": [
    {
      "date": "2026-04-12",
      "sbp": 134,
      "dbp": 86,
      "source": "clinic"
    },
    {
      "date": "2026-04-28",
      "sbp": 135,
      "dbp": 85,
      "source": "sms"
    },
    "... 7 more"
  ],
  "baseline": {
    "sbp": 134.0,
    "dbp": 85.0,
    "readings": 3
  },
  "change_sbp": 12.0,
  "slope_sbp": 1.91,
  "flags": [],
  "suggestion": {
    "decision_id": 222,
    "date": "2026-09-29",
    "suggestion": "routine",
    "probs": {
      "routine": 1.0
    },
    "reason_text": null,
    "guideline_ref": null,
    "state_text": "Flags: none.\nRule default: routine.\nMedicine taken at the last three contacts: yes, yes, yes.\nDays since last contact: 0.\nCHW notes:\n\"Mgonjw...",
    "final_choice": "routine",
    "override_reason": null,
    "model_version": "synthetic-history",
    "config_version": "2026-10-04a"
  },
  "actions": [
    "routine",
    "recheck_7_days",
    "... 4 more"
  ],
  "medications": [
    {
      "id": 2,
      "patient_id": 1,
      "encounter_id": 5,
      "name": "amlodipine",
      "dose_text": "5 mg once daily",
      "source_text": "Amlodipine 5 mg once daily. Reduce salt."
    }
  ],
  "medication_history": [
    {
      "id": 2,
      "patient_id": 1,
      "encounter_id": 5,
      "name": "amlodipine",
      "dose_text": "5 mg once daily",
      "source_text": "Amlodipine 5 mg once daily. Reduce salt."
    },
    {
      "id": 1,
      "patient_id": 1,
      "encounter_id": 1,
      "name": "amlodipine",
      "dose_text": "5 mg once daily",
      "source_text": "Amlodipine 5 mg once daily. Reduce salt."
    }
  ],
  "understanding": {
    "medication": 1.0,
    "salt_and_diet": 1.0,
    "danger_signs": null,
    "when_to_seek_care": null,
    "what_bp_means": null
  },
  "quiz_history": [
    {
      "id": 6,
      "patient_id": 1,
      "question_id": "q06",
      "topic": "salt_and_diet",
      "asked_at": "2026-09-22T09:00:00",
      "channel": "sms",
      "answer": "1",
      "correct": "yes",
      "lang": "sw",
      "next_due": "2026-10-22"
    },
  
```

### GET /patients/1/summary
```json
{
  "lines": [
    "Dernier contact le 2026-09-29 par CHW : TA 146/92.",
    "Médicaments pris ; pas de symptômes signalés.",
    "... 1 more"
  ],
  "source": "hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M",
  "facts": {
    "contacts": [
      {
        "date": "2026-09-29",
        "source": "chw",
        "bp": "146/92",
        "medicine_taken": "yes",
        "symptoms": []
      },
      {
        "date": "2026-09-03",
        "source": "sms",
        "bp": "143/90",
        "medicine_taken": "yes",
        "symptoms": []
      }
    ],
    "open_flags": []
  },
  "reason": "checks passed"
}
```

### GET /escalation?lang=fr
```json
{
  "protocol": {
    "lang": "fr",
    "requested_lang": "fr",
    "text": "**Quand cette alerte apparaît**\n\n- Tout signe FAST, quelle que soit la tension : visage affaissé d'un côté, bras ou jambe soudainement faibl...",
    "status": "Status: draft for physician sign-off. Square brackets mark the local facts the physician fills in. The engine shows this text in the escalat...",
    "file": "docs/emergency_protocol.md",
    "source": "Program protocol; square brackets are local facts the physician fills in",
    "sources_text": "- WHO HEARTS technical package: severe blood pressure (180/110 or higher) is treated as urgent, and as an emergency when symptoms of organ d..."
  },
  "approved": false,
  "patients": [
    {
      "patient_id": 17,
      "age": 44,
      "village": "Kirundo",
      "reasons": [
        "Symptom 'speech_difficulty' reported on 2026-10-01 - on the emergency symptom list."
      ],
      "flags": [
        {
          "id": 11,
          "patient_id": 17,
          "date": "2026-10-03",
          "type": "symptom",
          "value": "speech_difficulty",
          "reason_text": "Symptom 'speech_difficulty' reported on 2026-10-01 - on the emergency symptom list.",
          "guideline_ref": "config.symptoms_emergency; FAST",
          "emergency": "yes"
        }
      ]
    },
    {
      "patient_id": 21,
      "age": 50,
      "village": "Rutana",
      "reasons": [
        "Latest BP 171/113 on 2026-09-30 is at or above the 'severe_with_symptoms' cut-off (180/110). Symptom present: headache."
      ],
      "flags": [
        {
          "id": 16,
          "patient_id": 21,
          "date": "2026-10-03",
          "type": "threshold",
          "value": "171/113",
          "reason_text": "Latest BP 171/113 on 2026-09-30 is at or above the 'severe_with_symptoms' cut-off (180/110). Symptom present: headache.",
          "guideline_ref": "ISH2020; HEARTS",
          "emergency": "yes"
        }
      ]
    },
    "... 1 more"
  ]
}
```

### GET /protocol?lang=en
```json
{
  "lang": "en",
  "requested_lang": "en",
  "text": "**When this box appears**\n\n- Any FAST sign, at any blood pressure: face drooping on one side, sudden weak arm or leg, sudden trouble speakin...",
  "status": "Status: draft for physician sign-off. Square brackets mark the local facts the physician fills in. The engine shows this text in the escalat...",
  "file": "docs/emergency_protocol.md",
  "source": "Program protocol; square brackets are local facts the physician fills in",
  "sources_text": "- WHO HEARTS technical package: severe blood pressure (180/110 or higher) is treated as urgent, and as an emergency when symptoms of organ d..."
}
```

### POST /messages/incoming
Body:
```json
{"patient_id": 1, "text": "Habari. Leo presha yangu ni 150/95. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa."}
```
```json
{
  "message_id": 808,
  "routed_to": "inbox",
  "parsed": {
    "message_id": 808,
    "text": "Habari. Leo presha yangu ni 150/95. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa.",
    "intent": "bp_report",
    "intent_probs": {
      "bp_report": 0.9497678517213697,
      "meds_question": 0.011231255732344009,
      "other": 0.009881299921552187,
      "symptom": 0.029119592624734285
    },
    "intent_backend": "tfidf-lr",
    "regex_bp": [
      150,
      95
    ],
    "llm": {
      "valid": true,
      "reason": "all four checks passed",
      "model": "hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M"
    },
    "fields": {
      "sbp": 150,
      "dbp": 95,
      "meds_taken": "partial",
      "symptoms": [
        "headache"
      ],
      "free_text_rest": "Habari. Leo presha yangu ni 150/95. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa."
    },
    "valid": true,
    "needs_manual": false,
    "possible_emergency": false,
    "reason": "all four checks passed",
    "processed_at": "2026-10-03T23:57:03",
    "confirmed": false
  }
}
```

### GET /inbox?status=needs_review
```json
{
  "count": 1,
  "messages": [
    {
      "id": 808,
      "patient_id": 1,
      "direction": "in",
      "kind": "report",
      "lang": "sw",
      "text": "Habari. Leo presha yangu ni 150/95. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa.",
      "received_at": "2026-10-03T23:56:59",
      "parsed_json": {
        "intent": "bp_report",
        "intent_probs": {
          "bp_report": 0.9497678517213697,
          "meds_question": 0.011231255732344009,
          "other": 0.009881299921552187,
          "symptom": 0.029119592624734285
        },
        "intent_backend": "tfidf-lr",
        "regex_bp": [
          150,
          95
        ],
        "llm": {
          "valid": true,
          "reason": "all four checks passed",
          "model": "hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M"
        },
        "fields": {
          "sbp": 150,
          "dbp": 95,
          "meds_taken": "partial",
          "symptoms": [
            "headache"
          ],
          "free_text_rest": "Habari. Leo presha yangu ni 150/95. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa."
        },
        "valid": true,
        "needs_manual": false,
        "possible_emergency": false,
        "reason": "all four checks passed",
        "processed_at": "2026-10-03T23:57:03",
        "confirmed": false
      },
      "encounter_id": null,
      "status": null,
      "state": "needs_review"
    }
  ]
}
```

### POST /messages/808/process
```json
{
  "message_id": 808,
  "text": "Habari. Leo presha yangu ni 150/95. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa.",
  "intent": "bp_report",
  "intent_probs": {
    "bp_report": 0.9497678517213697,
    "meds_question": 0.011231255732344009,
    "other": 0.009881299921552187,
    "symptom": 0.029119592624734285
  },
  "intent_backend": "tfidf-lr",
  "regex_bp": [
    150,
    95
  ],
  "llm": {
    "valid": true,
    "reason": "all four checks passed",
    "model": "hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M"
  },
  "fields": {
    "sbp": 150,
    "dbp": 95,
    "meds_taken": "partial",
    "symptoms": [
      "headache"
    ],
    "free_text_rest": "Habari. Leo presha yangu ni 150/95. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa."
  },
  "valid": true,
  "needs_manual": false,
  "possible_emergency": false,
  "reason": "all four checks passed",
  "processed_at": "2026-10-03T23:57:07",
  "confirmed": false
}
```

### POST /messages/808/confirm
Body:
```json
{"fields": {"sbp": 150, "dbp": 95, "meds_taken": "partial", "symptoms": ["headache"]}}
```
```json
{
  "message_id": 808,
  "patient_id": 1,
  "encounter_id": 1728,
  "intent": "bp_report",
  "fields": {
    "sbp": 150,
    "dbp": 95,
    "meds_taken": "partial",
    "symptoms": [
      "headache"
    ],
    "free_text_rest": ""
  },
  "flags": [
    {
      "type": "symptom",
      "value": "headache",
      "reason_text": "Symptom 'headache' reported on 2026-10-03; latest BP 150/95 is not under the goal 140/90 (bp_goal).",
      "guideline_ref": "config.symptoms_other",
      "emergency": "no",
      "action": "visit_this_week"
    }
  ],
  "suggestion": {
    "patient_id": 1,
    "date": "2026-10-03",
    "state_text": "Flag symptom: Symptom 'headache' reported on 2026-10-03; latest BP 150/95 is not under the goal 140/90 (bp_goal).\nRule default: visit_this_w...",
    "flags": [
      {
        "type": "symptom",
        "value": "headache",
        "reason_text": "Symptom 'headache' reported on 2026-10-03; latest BP 150/95 is not under the goal 140/90 (bp_goal).",
        "guideline_ref": "config.symptoms_other",
        "emergency": "no",
        "action": "visit_this_week"
      }
    ],
    "rule_default": "visit_this_week",
    "rule_action": "visit_this_week",
    "moved": false,
    "direction": null,
    "moved_by": null,
    "guardrail": null,
    "config_version": "2026-10-04a",
    "approved": false,
    "suggestion": "visit_this_week",
    "probs": {
      "visit_this_week": 1.0
    },
    "backend": "rules",
    "model_version": "rules-v1",
    "reason_text": "Visit this week (rules only (no model available)). Symptom 'headache' reported on 2026-10-03; latest BP 150/95 is not under the goal 140/90 ...",
    "guideline_ref": "config.symptoms_other",
    "decision_id": 249
  },
  "risk": {
    "patient_id": 1,
    "date": "2026-10-03",
    "score": 16.5,
    "tier": "high",
    "components": {
      "who_base": {
        "points": 2.5,
        "percent_10y": 5,
        "reasons": [
          "WHO 10-year CVD risk 5% (woman, non-smoker, age 55-59, SBP 140-159, BMI 20-24 (23.5))."
        ],
        "note": null
      },
      "symptom_flag": {
        "points": 10,
        "reasons": [
          "Symptom 'headache' reported on 2026-10-03; latest BP 150/95 is not under the goal 140/90 (bp_goal)."
        ]
      },
      "missed_meds": {
        "points": 2,
        "reasons": [
          "Medicine taken: partial on 2026-10-03."
        ]
      },
      "missed_appointment": {
        "points": 2,
        "reasons": [
          "1 missed cli
```

### POST /patients/1/decide
```json
{
  "patient_id": 1,
  "date": "2026-10-03",
  "state_text": "Flag symptom: Symptom 'headache' reported on 2026-10-03; latest BP 150/95 is not under the goal 140/90 (bp_goal).\nRule default: visit_this_w...",
  "flags": [
    {
      "type": "symptom",
      "value": "headache",
      "reason_text": "Symptom 'headache' reported on 2026-10-03; latest BP 150/95 is not under the goal 140/90 (bp_goal).",
      "guideline_ref": "config.symptoms_other",
      "emergency": "no",
      "action": "visit_this_week"
    }
  ],
  "rule_default": "visit_this_week",
  "rule_action": "visit_this_week",
  "moved": false,
  "direction": null,
  "moved_by": null,
  "guardrail": null,
  "config_version": "2026-10-04a",
  "approved": false,
  "suggestion": "visit_this_week",
  "probs": {
    "visit_this_week": 1.0
  },
  "backend": "rules",
  "model_version": "rules-v1",
  "reason_text": "Visit this week (rules only (no model available)). Symptom 'headache' reported on 2026-10-03; latest BP 150/95 is not under the goal 140/90 ...",
  "guideline_ref": "config.symptoms_other",
  "decision_id": 249
}
```

### POST /patients/1/confirm_decision
Body:
```json
{"final_choice": "recheck_7_days", "override_reason": "patient travelling"}
```
```json
{
  "decision_id": 249,
  "suggestion": "visit_this_week",
  "final_choice": "recheck_7_days",
  "overridden": true,
  "override_reason": "patient travelling",
  "urgent": false,
  "followups": [
    {
      "id": 395,
      "kind": "sms_check",
      "due_date": "2026-10-10"
    }
  ],
  "followup": {
    "id": 395,
    "kind": "sms_check",
    "due_date": "2026-10-10"
  },
  "model_version": "rules-v1",
  "config_version": "2026-10-04a"
}
```

### GET /outgoing
```json
{
  "count": 19,
  "messages": [
    {
      "id": 809,
      "patient_id": 1,
      "direction": "out",
      "kind": "quiz",
      "lang": "sw",
      "text": "Ipi ni dalili ya hatari ya kiharusi?\n1) Uso kulegea upande mmoja\n2) Kusikia njaa\n3) Mafua\nJibu 1, 2 au 3",
      "received_at": "2026-10-03T23:57:07",
      "parsed_json": {
        "question_id": "q07",
        "quiz_result_id": 75
      },
      "encounter_id": null,
      "status": "queued",
      "village": "Kirundo"
    },
    {
      "id": 810,
      "patient_id": 48,
      "direction": "out",
      "kind": "reminder",
      "lang": "fr",
      "text": "Bonjour. Rappel de votre agent de santé : vous avez un rendez-vous au centre de santé le 2026-10-04. Merci de venir avec vos médicaments.",
      "received_at": "2026-10-03T23:57:07",
      "parsed_json": {
        "template": "reminder_appointment",
        "verified": false,
        "followup_id": 235
      },
      "encounter_id": null,
      "status": "queued",
      "village": "Cibitoke"
    },
    "... 17 more"
  ]
}
```

### POST /sync
```json
{
  "sent": [
    809,
    810,
    "... 17 more"
  ]
}
```

### GET /phone/1
```json
{
  "patient_id": 1,
  "language": "sw",
  "messages": [
    {
      "id": 1,
      "direction": "in",
      "kind": "report",
      "lang": "sw",
      "text": "presha leo 135/85",
      "at": "2026-04-28T07:30:00",
      "status": null,
      "delivered": true,
      "state": "confirmed"
    },
    {
      "id": 2,
      "direction": "in",
      "kind": "report",
      "lang": "sw",
      "text": "Mimi ni mzima. Presha ni 136/86, nakunywa dawa kila siku.",
      "at": "2026-06-06T07:30:00",
      "status": null,
      "delivered": true,
      "state": "confirmed"
    },
    "... 5 more"
  ]
}
```

### POST /messages/incoming
Body:
```json
{"patient_id": 1, "text": "2"}
```
```json
{
  "message_id": 828,
  "routed_to": "quiz",
  "quiz": {
    "patient_id": 1,
    "question_id": "q07",
    "topic": "danger_signs",
    "reply": "2",
    "option": 2,
    "matched_by": "digit",
    "correct": "no",
    "next_due": "2026-10-10",
    "correction_message_id": 829,
    "understanding": {
      "medication": 1.0,
      "salt_and_diet": 1.0,
      "danger_signs": 0.0,
      "when_to_seek_care": null,
      "what_bp_means": null
    }
  }
}
```

### POST /messages/829/sent
```json
{
  "message_id": 829,
  "status": "sent"
}
```

### POST /quiz/next
Body:
```json
{"patient_id": 2, "channel": "sms"}
```
```json
{
  "patient_id": 2,
  "question_id": "q13",
  "topic": "what_bp_means",
  "text": "Je, presha inaweza kuwa juu ukijisikia vizuri?\n1) Hapana, ningejisikia mgonjwa\n2) Ndiyo, mara nyingi bila dalili\n3) Kwa wazee tu\nJibu 1, 2 a...",
  "lang": "sw",
  "channel": "sms",
  "message_id": 830,
  "quiz_result_id": 76,
  "already_pending": false
}
```

### POST /quiz/answer
Body:
```json
{"patient_id": 2, "question_id": "q13", "reply_text": "1"}
```
```json
{
  "patient_id": 2,
  "question_id": "q13",
  "topic": "what_bp_means",
  "reply": "1",
  "option": 1,
  "matched_by": "digit",
  "correct": "no",
  "next_due": "2026-10-10",
  "correction_message_id": 831,
  "understanding": {
    "medication": null,
    "salt_and_diet": null,
    "danger_signs": 1.0,
    "when_to_seek_care": null,
    "what_bp_means": 0.5
  }
}
```

### GET /quiz/support
```json
{
  "cutoff": 0.5,
  "patients": [
    {
      "patient_id": 1,
      "low_topics": {
        "danger_signs": 0.0
      }
    },
    {
      "patient_id": 3,
      "low_topics": {
        "what_bp_means": 0.0
      }
    },
    "... 9 more"
  ],
  "count_by_topic": {
    "medication": 1,
    "salt_and_diet": 1,
    "danger_signs": 6,
    "when_to_seek_care": 2,
    "what_bp_means": 1
  }
}
```

### POST /risk/run
Body:
```json
{}
```
```json
{
  "date": "2026-10-03",
  "count": 60,
  "tiers": {
    "high": 18,
    "medium": 28,
    "low": 14
  },
  "who_table_loaded": true,
  "scores": [
    {
      "patient_id": 10,
      "date": "2026-10-03",
      "score": 29.5,
      "tier": "high",
      "components": {
        "who_base": {
          "points": 6.5,
          "percent_10y": 13,
          "reasons": [
            "WHO 10-year CVD risk 13% (woman, smoker, age 55-59, SBP 160-179, BMI 20-24 (22.5))."
          ],
          "note": null
        },
        "threshold_flag": {
          "points": 4,
          "reasons": [
            "Latest BP 167/101 on 2026-10-01 is at or above the 'grade2' cut-off (160/100)."
          ]
        },
        "trend_flag": {
          "points": 3,
          "reasons": [
            "Trend rule: each of the last 3 readings (157, 172, 167, 2026-09-18 to 2026-10-01) is at least 10 mmHg above the baseline of 140; and the las..."
          ]
        },
        "symptom_flag": {
          "points": 10,
          "reasons": [
            "Symptom 'headache' reported on 2026-10-01; latest BP 167/101 is not under the goal 140/90 (bp_goal)."
          ]
        },
        "missed_meds": {
          "points": 2,
          "reasons": [
            "Medicine taken: partial on 2026-10-01."
          ]
        },
        "trajectory": {
          "points": 4,
          "reasons": [
            "7 of the 10 most similar local patient patterns were followed by a stroke or admission within 90 days (weighted share 0.66)."
          ]
        }
      },
      "emergency": false,
      "config_version": "2026-10-04a"
    },
    {
      "patient_id": 27,
      "date": "2026-10-03",
      "score": 28.0,
      "tier": "high",
      "components": {
        "who_base": {
          "points": 11.0,
          "percent_10y": 22,
          "reasons": [
            "WHO 10-year CVD risk 22% (man, non-smoker, age 70-74, SBP 160-179, BMI 25-29 (27.7))."
          ],
          "note": null
        },
        "threshold_flag": {
          "points": 4,
          "reasons": [
            "Latest BP 160/99 on 2026-10-02 is at or above the 'grade2' cut-off (160/100)."
          ]
        },
        "trend_flag": {
          "points": 3,
          "reasons": [
            "Trend rule: each of the last 3 readings (142, 153, 160, 2026-09-16 to 2026-10-02) is at least 10 mmHg above the baseline of 130; and the las..."
          ]
        },
        "symptom_flag": {
          "points": 10,
          "rea
```

### GET /plan?chw=1&week_start=2026-10-05&hours=20
```json
{
  "chw_id": 1,
  "week_start": "2026-10-05",
  "hours": 20.0,
  "minutes_used": 1200,
  "minutes_left": 0,
  "hours_used": 20.0,
  "hours_left": 0.0,
  "approved": false,
  "approved_at": null,
  "counts": {
    "visit": 35,
    "sms": 7,
    "wait": 15
  },
  "route": [
    {
      "village": "Kirundo",
      "patient_ids": [
        18,
        53,
        "... 5 more"
      ]
    },
    {
      "village": "Ngozi",
      "patient_ids": [
        10,
        9,
        "... 7 more"
      ]
    },
    "... 4 more"
  ],
  "rows": [
    {
      "id": 1,
      "chw_id": 1,
      "week_start": "2026-10-05",
      "patient_id": 18,
      "action": "visit",
      "rank": 1,
      "est_minutes": 38.6,
      "reason_text": "Visit: high tier, score 22 (who base, threshold flag). Kirundo route, about 39 min, incl. 14 min travel.",
      "confirmed": null,
      "done_on": null,
      "village": "Kirundo",
      "age": 77,
      "sex": "male",
      "score": 22.0,
      "tier": "high"
    },
    {
      "id": 2,
      "chw_id": 1,
      "week_start": "2026-10-05",
      "patient_id": 53,
      "action": "visit",
      "rank": 2,
      "est_minutes": 24.2,
      "reason_text": "Visit: high tier, score 22 (who base, threshold flag). Kirundo route, about 24 min, travel shared with other visits there.",
      "confirmed": null,
      "done_on": null,
      "village": "Kirundo",
      "age": 77,
      "sex": "female",
      "score": 22.0,
      "tier": "high"
    },
    "... 55 more"
  ],
  "escalation_patient_ids": [
    17,
    21,
    "... 1 more"
  ]
}
```

### POST /plan/43/update
Body:
```json
{"action": "visit"}
```
```json
{
  "chw_id": 1,
  "week_start": "2026-10-05",
  "hours": 20.0,
  "minutes_used": 1292,
  "minutes_left": -92,
  "hours_used": 21.5,
  "hours_left": -1.5,
  "approved": false,
  "approved_at": null,
  "counts": {
    "visit": 36,
    "sms": 7,
    "wait": 14
  },
  "route": [
    {
      "village": "Kirundo",
      "patient_ids": [
        18,
        53,
        "... 5 more"
      ]
    },
    {
      "village": "Ngozi",
      "patient_ids": [
        10,
        9,
        "... 7 more"
      ]
    },
    "... 4 more"
  ],
  "rows": [
    {
      "id": 1,
      "chw_id": 1,
      "week_start": "2026-10-05",
      "patient_id": 18,
      "action": "visit",
      "rank": 1,
      "est_minutes": 38.6,
      "reason_text": "Visit: high tier, score 22 (who base, threshold flag). Kirundo route, about 39 min, incl. 14 min travel.",
      "confirmed": null,
      "done_on": null,
      "village": "Kirundo",
      "age": 77,
      "sex": "male",
      "score": 22.0,
      "tier": "high"
    },
    {
      "id": 2,
      "chw_id": 1,
      "week_start": "2026-10-05",
      "patient_id": 53,
      "action": "visit",
      "rank": 2,
      "est_minutes": 24.2,
      "reason_text": "Visit: high tier, score 22 (who base, threshold flag). Kirundo route, about 24 min, travel shared with other visits there.",
      "confirmed": null,
      "done_on": null,
      "village": "Kirundo",
      "age": 77,
      "sex": "female",
      "score": 22.0,
      "tier": "high"
    },
    "... 55 more"
  ],
  "escalation_patient_ids": [
    17,
    21,
    "... 1 more"
  ]
}
```

### POST /plan/approve
Body:
```json
{"chw_id": 1, "week_start": "2026-10-05"}
```
```json
{
  "chw_id": 1,
  "week_start": "2026-10-05",
  "hours": 20.0,
  "minutes_used": 1292,
  "minutes_left": -92,
  "hours_used": 21.5,
  "hours_left": -1.5,
  "approved": true,
  "approved_at": "2026-10-03T23:57:07",
  "counts": {
    "visit": 36,
    "sms": 7,
    "wait": 14
  },
  "route": [
    {
      "village": "Kirundo",
      "patient_ids": [
        18,
        53,
        "... 5 more"
      ]
    },
    {
      "village": "Ngozi",
      "patient_ids": [
        10,
        9,
        "... 7 more"
      ]
    },
    "... 4 more"
  ],
  "rows": [
    {
      "id": 1,
      "chw_id": 1,
      "week_start": "2026-10-05",
      "patient_id": 18,
      "action": "visit",
      "rank": 1,
      "est_minutes": 38.6,
      "reason_text": "Visit: high tier, score 22 (who base, threshold flag). Kirundo route, about 39 min, incl. 14 min travel.",
      "confirmed": "2026-10-03T23:57:07",
      "done_on": null,
      "village": "Kirundo",
      "age": 77,
      "sex": "male",
      "score": 22.0,
      "tier": "high"
    },
    {
      "id": 2,
      "chw_id": 1,
      "week_start": "2026-10-05",
      "patient_id": 53,
      "action": "visit",
      "rank": 2,
      "est_minutes": 24.2,
      "reason_text": "Visit: high tier, score 22 (who base, threshold flag). Kirundo route, about 24 min, travel shared with other visits there.",
      "confirmed": "2026-10-03T23:57:07",
      "done_on": null,
      "village": "Kirundo",
      "age": 77,
      "sex": "female",
      "score": 22.0,
      "tier": "high"
    },
    "... 55 more"
  ],
  "escalation_patient_ids": [
    17,
    21,
    "... 1 more"
  ]
}
```

### GET /hours?chw=1
```json
{
  "chw_id": 1,
  "week_start": "2026-09-28",
  "by_activity": [
    {
      "activity": "visit",
      "minutes": 481,
      "hours": 8.0,
      "count": 17
    },
    {
      "activity": "sms",
      "minutes": 48,
      "hours": 0.8,
      "count": 16
    },
    "... 2 more"
  ],
  "total_hours": 18.4,
  "capacity_hours": 20,
  "villages": [
    {
      "village": "Cibitoke",
      "visit": 35.2,
      "travel": 56.7,
      "n_visits": 135
    },
    {
      "village": "Gitega",
      "visit": 24.8,
      "travel": 19.8,
      "n_visits": 132
    },
    "... 4 more"
  ]
}
```

### POST /time
Body:
```json
{"chw_id": 1, "activity": "visit", "patient_id": 1, "start": "2026-10-03T09:00:00", "end": "2026-10-03T09:40:00"}
```
```json
{
  "id": 2264,
  "chw_id": 1,
  "activity": "visit",
  "patient_id": 1,
  "village": "Kirundo",
  "minutes": 40.0,
  "quiz_question": {
    "patient_id": 1,
    "question_id": "q10",
    "topic": "when_to_seek_care",
    "text": "Uso unalegea au mkono unakosa nguvu ghafla. Ufanye nini?\n1) Nisubiri hadi kesho\n2) Nilale nipumzike\n3) Niende kituo cha afya sasa\nJibu 1, 2 ...",
    "lang": "sw",
    "channel": "visit",
    "message_id": null,
    "quiz_result_id": 77,
    "already_pending": false
  }
}
```

### POST /patients/2/goal_override
Body:
```json
{"goal_override": "high_risk", "pin": "4821"}
```
```json
{
  "patient_id": 2,
  "goal_override": "high_risk",
  "goal": {
    "sbp_lt": 130,
    "dbp_lt": 80,
    "source": "WHO2021",
    "id": "bp_goal_high_risk"
  },
  "set_by": "Supervisor (example)",
  "flags": []
}
```

### POST /inbox/process
Body:
```json
{"limit": 100}
```
```json
{
  "processed": 1,
  "still_waiting": 0,
  "needs_manual": 0,
  "possible_emergency": []
}
```

### GET /followups?patient_id=1
```json
{
  "due": [
    {
      "id": 395,
      "patient_id": 1,
      "due_date": "2026-10-10",
      "kind": "sms_check",
      "status": "due",
      "done_on": null,
      "village": "Kirundo"
    },
    {
      "id": 4,
      "patient_id": 1,
      "due_date": "2026-10-14",
      "kind": "clinic_appointment",
      "status": "due",
      "done_on": null,
      "village": "Kirundo"
    }
  ],
  "missed": [
    {
      "id": 3,
      "patient_id": 1,
      "due_date": "2026-09-03",
      "kind": "clinic_appointment",
      "status": "missed",
      "done_on": null,
      "village": "Kirundo"
    }
  ],
  "done": [
    {
      "id": 1,
      "patient_id": 1,
      "due_date": "2026-04-12",
      "kind": "clinic_appointment",
      "status": "done",
      "done_on": "2026-04-12",
      "village": "Kirundo"
    },
    {
      "id": 2,
      "patient_id": 1,
      "due_date": "2026-06-27",
      "kind": "clinic_appointment",
      "status": "done",
      "done_on": "2026-06-27",
      "village": "Kirundo"
    },
    "... 1 more"
  ]
}
```

### GET /patients/1/trajectory
```json
{
  "patient_id": 1,
  "share": 0.32,
  "flag": "no",
  "peers": [
    {
      "peer": "peer-A",
      "had_event_within_horizon": true,
      "distance": 2.62,
      "sbp": [
        133,
        128,
        "... 6 more"
      ],
      "dates": [
        "2025-12-17",
        "2026-01-14",
        "... 6 more"
      ],
      "days_to_event": 2
    },
    {
      "peer": "peer-B",
      "had_event_within_horizon": false,
      "distance": 3.31,
      "sbp": [
        140,
        145,
        "... 6 more"
      ],
      "dates": [
        "2026-05-01",
        "2026-05-08",
        "... 6 more"
      ],
      "days_to_event": null
    },
    "... 1 more"
  ],
  "reason_text": "3 of the 10 most similar local patient patterns were followed by a stroke or admission within 90 days (weighted share 0.32)."
}
```

### GET /metrics?month=2026-09
```json
{
  "month": "2026-09",
  "config_version": "2026-10-04a",
  "suggestions": {
    "made": 83,
    "confirmed": 83,
    "accepted": 69,
    "override_rate": 0.169,
    "override_up_rate": 0.157,
    "override_down_rate": 0.012,
    "not_sure_rate": 0.0
  },
  "days_flag_to_contact": {
    "mean": null,
    "n": 0
  },
  "followups": {
    "due": 55,
    "done": 39,
    "missed": 16,
    "missed_clinic_appointments": 6
  },
  "overdue_now": 27,
  "hours_per_week": 20.6,
  "hours_by_activity": {
    "visit": 37.5,
    "travel": 41.9,
    "sms": 5.1,
    "admin": 4.0
  },
  "planned_visits": {
    "planned": 0,
    "completed": 0,
    "share": null
  },
  "share_visits_high_tier": 0.338,
  "understanding_by_topic": {
    "medication": 0.857,
    "salt_and_diet": 1.0,
    "danger_signs": 0.5,
    "when_to_seek_care": 0.875,
    "what_bp_means": 1.0
  },
  "share_patients_know_danger_signs": {
    "share": 0.571,
    "asked": 7
  },
  "bp": {
    "patients_with_reading": 55,
    "at_goal": 19,
    "share_at_goal": 0.345,
    "mean_change_from_baseline_sbp": 7.9,
    "goal": {
      "sbp_lt": 140,
      "dbp_lt": 90,
      "source": "WHO2021; ISH2020"
    },
    "goal_high_risk": {
      "sbp_lt": 130,
      "dbp_lt": 80,
      "source": "WHO2021"
    },
    "patients_on_high_risk_goal": 1
  },
  "referrals": {
    "made": 1,
    "completed": 0
  },
  "events": 1,
  "events_with_prior_flag": {
    "events": 1,
    "with_prior_flag": 1
  },
  "low_yield_visits": 0,
  "error_reviews": {
    "detected": 0,
    "by_kind": {
      "false_positive": 0,
      "false_negative": 0,
      "extraction_error": 0
    },
    "pending": 0,
    "confirmed_false_positives": 0,
    "confirmed_false_negatives": 0
  }
}
```

### POST /errors/detect
Body:
```json
{}
```
```json
{
  "new": {
    "override": 28,
    "event_without_flag": 6,
    "referral_sent_home": 2,
    "field_corrected": 4,
    "trajectory_miss": 0
  },
  "month": null,
  "total": 40,
  "by_kind": {
    "extraction_error": 4,
    "false_negative": 30,
    "false_positive": 6
  },
  "by_source": {
    "event_without_flag": 6,
    "field_corrected": 4,
    "override_down": 4,
    "override_up": 24,
    "referral_sent_home": 2
  },
  "by_label": {
    "pending": 40
  },
  "confirmed_by_cause": {
    "extraction": 0
  },
  "per_rule": {
    "extraction:dbp": {
      "flags_raised": 0,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 3,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy": null
    },
    "extraction:symptoms": {
      "flags_raised": 0,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 1,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy": null
    },
    "none": {
      "flags_raised": 0,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 27,
      "low_yield_visits": 1,
      "flags_followed_by_action": 0,
      "precision_proxy": null
    },
    "symptom:arm_weakness": {
      "flags_raised": 1,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 0,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy": 0.0
    },
    "symptom:headache": {
      "flags_raised": 10,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 4,
      "low_yield_visits": 0,
      "flags_followed_by_action": 1,
      "precision_proxy": 0.1
    },
    "symptom:speech_difficulty": {
      "flags_raised": 1,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 0,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy": 0.0
    },
    "threshold": {
      "flags_raised": 0,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 7,
      "low_yield_visits": 1,
      "flags_followed_by_action": 0,
      "precision_proxy": null
    },
    "threshold:grade2": {
      "flags_raised": 17,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 0,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy":
```

### GET /errors?label=pending
```json
{
  "reviews": [
    {
      "id": 40,
      "patient_id": 37,
      "kind": "extraction_error",
      "source": "field_corrected",
      "linked_id": 504,
      "rule_id": "extraction:dbp",
      "model_version": "hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M",
      "config_version": "2026-10-04a",
      "detected_on": "2026-10-03T23:57:10",
      "supervisor_label": "pending",
      "cause_code": "extraction",
      "reviewed_by": null,
      "reviewed_on": null,
      "evidence": {
        "message_id": 504,
        "text": "Habari daktari, presha yangu ni 129 kwa 78. Nimekunywa dawa zangu.",
        "changed": {
          "dbp": {
            "extracted": 88,
            "confirmed": 78
          }
        },
        "intent": "bp_report"
      }
    },
    {
      "id": 39,
      "patient_id": 24,
      "kind": "extraction_error",
      "source": "field_corrected",
      "linked_id": 339,
      "rule_id": "extraction:dbp",
      "model_version": "hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M",
      "config_version": "2026-10-04a",
      "detected_on": "2026-10-03T23:57:10",
      "supervisor_label": "pending",
      "cause_code": "extraction",
      "reviewed_by": null,
      "reviewed_on": null,
      "evidence": {
        "message_id": 339,
        "text": "Tension 138/83. J'ai pris un seul médicament sur les deux.",
        "changed": {
          "dbp": {
            "extracted": 93,
            "confirmed": 83
          }
        },
        "intent": "bp_report"
      }
    },
    "... 38 more"
  ]
}
```

### POST /errors/40/label
Body:
```json
{"supervisor_label": "confirmed_error", "cause_code": "extraction", "pin": "4821"}
```
```json
{
  "id": 40,
  "patient_id": 37,
  "kind": "extraction_error",
  "source": "field_corrected",
  "linked_id": 504,
  "rule_id": "extraction:dbp",
  "model_version": "hf.co/unsloth/medgemma-1.5-4b-it-GGUF:Q4_K_M",
  "config_version": "2026-10-04a",
  "detected_on": "2026-10-03T23:57:10",
  "evidence_json": "{\"message_id\": 504, \"text\": \"Habari daktari, presha yangu ni 129 kwa 78. Nimekunywa dawa zangu.\", \"changed\": {\"dbp\": {\"extracted\": 88, \"conf...",
  "supervisor_label": "confirmed_error",
  "cause_code": "extraction",
  "reviewed_by": "Supervisor (example)",
  "reviewed_on": "2026-10-03T23:57:10"
}
```

### GET /errors/summary
```json
{
  "month": null,
  "total": 40,
  "by_kind": {
    "extraction_error": 4,
    "false_negative": 30,
    "false_positive": 6
  },
  "by_source": {
    "event_without_flag": 6,
    "field_corrected": 4,
    "override_down": 4,
    "override_up": 24,
    "referral_sent_home": 2
  },
  "by_label": {
    "confirmed_error": 1,
    "pending": 39
  },
  "confirmed_by_cause": {
    "extraction": 1
  },
  "per_rule": {
    "extraction:dbp": {
      "flags_raised": 0,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 2,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy": null
    },
    "extraction:symptoms": {
      "flags_raised": 0,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 1,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy": null
    },
    "none": {
      "flags_raised": 0,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 27,
      "low_yield_visits": 1,
      "flags_followed_by_action": 0,
      "precision_proxy": null
    },
    "symptom:arm_weakness": {
      "flags_raised": 1,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 0,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy": 0.0
    },
    "symptom:headache": {
      "flags_raised": 10,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 4,
      "low_yield_visits": 0,
      "flags_followed_by_action": 1,
      "precision_proxy": 0.1
    },
    "symptom:speech_difficulty": {
      "flags_raised": 1,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 0,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy": 0.0
    },
    "threshold": {
      "flags_raised": 0,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 7,
      "low_yield_visits": 1,
      "flags_followed_by_action": 0,
      "precision_proxy": null
    },
    "threshold:grade2": {
      "flags_raised": 17,
      "confirmed_false_positive": 0,
      "confirmed_false_negative": 0,
      "pending": 0,
      "low_yield_visits": 0,
      "flags_followed_by_action": 0,
      "precision_proxy": 0.0
    },
    "threshold:severe_no_symptoms": {
      "flags_raised": 3,
      "confirmed_false_positive": 0,
      "
```

### POST /errors/replay
Body:
```json
{"changes": {"trend": {"rules": [{"sbp_rise_mmhg": 7, "consecutive_readings": 3}, {"sbp_rise_mmhg": 10, "over_readings": 4}]}}}
```
```json
{
  "changes": {
    "trend": {
      "rules": [
        {
          "sbp_rise_mmhg": 7,
          "consecutive_readings": 3
        },
        {
          "sbp_rise_mmhg": 10,
          "over_readings": 4
        }
      ]
    }
  },
  "contacts": 1669,
  "flags_now": 345,
  "flags_candidate": 429,
  "contacts_flagged_now": 242,
  "contacts_flagged_candidate": 305,
  "extra_contacts_flagged": 63,
  "flags_by_rule_now": {
    "symptom:headache": 21,
    "threshold:grade2": 128,
    "who_risk:high": 57,
    "trend": 128,
    "threshold:severe_no_symptoms": 6,
    "symptom:speech_difficulty": 1,
    "who_risk:very_high": 1,
    "threshold:severe_with_symptoms": 2,
    "symptom:arm_weakness": 1
  },
  "flags_by_rule_candidate": {
    "trend": 212,
    "symptom:headache": 21,
    "threshold:grade2": 128,
    "who_risk:high": 57,
    "threshold:severe_no_symptoms": 6,
    "symptom:speech_difficulty": 1,
    "who_risk:very_high": 1,
    "threshold:severe_with_symptoms": 2,
    "symptom:arm_weakness": 1
  },
  "events": 26,
  "events_with_prior_flag_now": 20,
  "events_gaining_prior_flag": [
    {
      "event_id": 10,
      "patient_id": 62,
      "date": "2026-05-10",
      "type": "stroke"
    },
    {
      "event_id": 15,
      "patient_id": 67,
      "date": "2026-07-27",
      "type": "stroke"
    }
  ],
  "events_losing_prior_flag": [],
  "seconds": 0.2
}
```

### POST /patients/3/referral_outcome
Body:
```json
{"outcome": "sent_home"}
```
```json
{
  "decision_id": 250,
  "final_choice": "refer_clinic",
  "outcome": "sent_home",
  "event_id": null
}
```

### POST /feedback
Body:
```json
{"who": "chw", "target": "suggestion", "value": "up", "note": ""}
```
```json
{
  "id": 1,
  "who": "chw",
  "target": "suggestion",
  "value": "up"
}
```

### POST /import
Body:
```json
{"csv_text": "patient_ref,sex,age,number_of_children,smoker,height_cm,weight_kg,village,language,date,source,complaint,tests_performed,positive_results,referral,doctor_recommendation,doctor_notes,chw_notes,bp,blood_sugar,blood_sugar_unit\nIMP-001,F,61,5,no,158,70,Ngozi,sw,2026-07-02,clinic,Kichwa kinauma,bp;blood_sugar,hypertension,,Amlodipine 5 mg once daily,BP high. Start treatment.,,158/96,6.1,
```
```json
{
  "patients_added": 2,
  "encounters_added": 5,
  "rows_not_read": [
    {
      "line": 7,
      "error": "cannot read BP 'high'"
    }
  ]
}
```

### GET /demo/script
```json
{
  "title": "TWESE CHW AI: the three-minute demo",
  "note": "Fictional patient on synthetic data. Swahili messages drafted by the engine team; the bilingual teammate checks them before recording.",
  "patient_id": 1,
  "patient": {
    "name": "Neema (fictional)",
    "age": 58,
    "sex": "female",
    "village": "Kirundo",
    "language": "sw"
  },
  "reset_route": "POST /demo/reset",
  "steps": [
    {
      "step": 1,
      "title": "Offline start",
      "actor": "presenter",
      "say": "WiFi is off. Everything runs on this laptop.",
      "message": null,
      "api": [
        "GET /status",
        "GET /patients"
      ],
      "expected": "Dashboard lists 60 synthetic patients (one CHW); banner: thresholds not yet physician-approved; patient 1 is low tier (score 4.5: WHO 10-yea..."
    },
    {
      "step": 2,
      "title": "A patient texts in Swahili",
      "actor": "patient",
      "message": {
        "lang": "sw",
        "text": "Habari. Leo presha yangu ni 150/95. Nimekunywa nusu ya dawa tu. Nina maumivu ya kichwa.",
        "gloss_en": "Hello. Today my blood pressure is 150/95. I took only half of my medicine. I have a headache."
      },
      "api": [
        "POST /messages/incoming",
        "GET /inbox?status=needs_review",
        "... 1 more"
      ],
      "expected": "Intent bp_report; fields sbp 150, dbp 95, meds_taken partial, symptoms [headache]; all four checks pass; the CHW taps Confirm."
    },
    "... 6 more"
  ],
  "notes_for_presenter": [
    "Run POST /demo/reset before each take.",
    "Escalation list: 3 patients. Severe BP without symptoms is an urgent clinic referral in 2 days, not an emergency.",
    "... 1 more"
  ]
}
```

### POST /review/run
```json
{
  "model_version": "lr-v1",
  "trained_at": "...",
  "decider": {
    "n_train": 1122,
    "n_test": 374,
    "accuracy": 0.941,
    "macro_f1": 0.685,
    "mean_top_probability": 0.933
  },
  "deterioration": {
    "auc_heldout_patients": 0.703
  },
  "learned_from": {
    "from_decisions_table": 233,
    "from_labeled_csv": 1263,
    "overrides": 37,
    "quiz_answers": 75
  },
  "saved": true,
  "previous_version": null
}
```

### POST /demo/reset
Body:
```json
{"seed": 1}
```
```json
{
  "reset": true,
  "seed": 1,
  "status": "{... same as GET /status}"
}
```
