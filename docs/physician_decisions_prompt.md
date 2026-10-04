# Prompt for Claude Code: apply the reviewed clinical decisions

Paste everything below the line into Claude Code.

---

Read CLAUDE.md first. Apply the reviewed clinical decisions below. Change only config/guideline_htn.json, content/quiz/patient_htn.json, the engine files named here, and their tests. Say in two lines what you will change, wait for "go", then do it.

Keep "approved": false in config/guideline_htn.json. Add "reviewed_by": "Jake, medical student, 2026-10-04" and "approval_note": "Cut-offs reviewed against WHO 2021, ISH 2020 and WHO HEARTS. Awaiting physician signature." Replace the existing "note" with that approval_note.

1. Sources. Add to "sources":
- {"id": "WHO2021", "title": "Guideline for the pharmacological treatment of hypertension in adults, WHO 2021", "url": "https://www.who.int/publications/i/item/9789240033986"}
- {"id": "HEARTS", "title": "WHO HEARTS technical package", "url": "https://www.who.int/publications/i/item/9789240001367"}
- {"id": "FAST", "title": "FAST stroke warning signs, American Stroke Association", "url": "https://www.stroke.org/en/about-stroke/stroke-symptoms"}

2. Thresholds. Keep the grade2 entry (160/100, visit_this_week, ISH2020). Replace the single "severe" entry with two:
- {"id": "severe_with_symptoms", "sbp_gte": 180, "dbp_gte": 110, "requires_any_symptom": true, "action": "emergency_now", "source": "ISH2020; HEARTS"}
- {"id": "severe_no_symptoms", "sbp_gte": 180, "dbp_gte": 110, "requires_any_symptom": false, "action": "refer_clinic", "urgent_days": 2, "source": "HEARTS"}
"Any symptom" means a symptom code other than "none" in the same encounter, or in an SMS from the same patient within the previous 24 hours. Update engine/signals.py so a threshold entry may carry requires_any_symptom and urgent_days, so severe_with_symptoms produces a flag with emergency = yes, and severe_no_symptoms produces a flag with emergency = no. The symptoms_emergency list is unchanged: FAST signs are emergency = yes at any blood pressure.

3. Trend rule. Replace "trend" with:
{"rules": [{"sbp_rise_mmhg": 10, "consecutive_readings": 3}, {"sbp_rise_mmhg": 15, "over_readings": 4}], "action": "recheck_7_days", "source": "Design choice, not a guideline number. ISH 2020 asks for repeat readings over 2 to 3 visits before acting on one reading, so this rule never fires on a single reading."}
In engine/signals.py: rule 1 fires when each of the last three readings is at least 10 mmHg systolic above baseline; rule 2 fires when the rise from baseline across the last four readings is at least 15 mmHg. Either rule produces one trend flag whose reason_text names the rule and the numbers.

4. Blood pressure goal. Keep "bp_goal": {"sbp_lt": 140, "dbp_lt": 90} and set its source to "WHO2021; ISH2020". Add "bp_goal_high_risk": {"sbp_lt": 130, "dbp_lt": 80, "source": "WHO2021"}. Add a column goal_override to the patients table (null or "high_risk"), set only by data/import_records.py or by a supervisor route, never by the engine. Wherever the goal is used (signals, metrics, the at-goal check in symptoms_other below), use bp_goal_high_risk when goal_override is "high_risk".

5. Non-emergency symptoms. Replace "symptoms_other_action" with:
"symptoms_other": {"at_goal": "recheck_7_days", "above_goal": "visit_this_week", "always_visit": ["dizziness"], "source": "Design choice. Dizziness may mean low pressure from treatment, so it always earns a visit."}
A symptom flag that is not an emergency gets the at_goal action when the latest BP is under the patient's goal, the above_goal action otherwise, and visit_this_week whenever the symptom is in always_visit.

6. Follow-up after each decision. Replace "followup_after_decision" with a list per action, and make engine/followups.py create every item in the list:
- "routine": [{"kind": "chw_visit", "days": 30}]  (WHO 2021: every 3 to 6 months once at target; monthly CHW contact kept for adherence)
- "recheck_7_days": [{"kind": "sms_check", "days": 7}]
- "visit_this_week": [{"kind": "chw_visit", "days": 7}, {"kind": "sms_check", "days": 14}]
- "refer_clinic": [{"kind": "clinic_appointment", "days": 7, "days_if_urgent": 2}, {"kind": "sms_check", "days_after_appointment": 3, "purpose": "confirm attendance"}]
- "emergency_now": [{"kind": "chw_visit", "days": 1}]
Use days_if_urgent when the flag that led to the decision carries urgent_days.

7. Emergency protocol. Replace "emergency_protocol" with {"file": "docs/emergency_protocol.md", "languages": ["en", "fr"], "source": "Program protocol; square brackets are local facts the physician fills in"}. Make api.escalation_list return the protocol text for the requested language from that file, English when the language is not in the file.

8. Quiz edits in content/quiz/patient_htn.json, English and French only. Keep every other question as it is. Add a top-level key "needs_retranslation": ["q06", "q12"] so step 12 knows which Swahili and Kirundi texts to redo.
- q06: options become 1 "Being active, like walking, most days", 2 "Resting all day", 3 "Drinking more alcohol"; correct stays 1. French: 1 "Bouger, comme marcher, presque tous les jours", 2 "Se reposer toute la journée", 3 "Boire plus d'alcool".
- q12: question becomes "Your medicine gives you a problem, like swollen feet or dizziness. What do you do?" French: "Votre médicament vous cause un problème, comme des pieds gonflés ou des vertiges. Que faites-vous ?" Options and correction unchanged.
- q07 to q11: change the source string "WHO stroke warning signs (FAST)" to "FAST stroke warning signs, American Stroke Association".

9. Tests. Update tests/test_signals.py for the two severe cases (180/115 with a headache gives emergency = yes; 180/115 with no symptom gives emergency = no and action refer_clinic), the two trend rules, and the dizziness case. Run `python -m pytest tests -q` and `python engine/evaluate.py` and report what changed.
