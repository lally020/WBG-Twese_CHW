# CHW Compass synthetic clinic workbook integration

This is a **fictional-only** demonstration. It does not provide validated patient triage, clinical advice, or real SMS transmission.

## Quick start (Mac)
1. Unzip the updated app in a *new folder*. Keep your existing app folder backed up.
2. In Terminal, `cd` into the app folder.
3. Run `bash run_workbook_demo_mac.command`.
4. On first launch, enter a 12+-character encryption passphrase. Enter the same passphrase when prompted to launch the app. In later runs, use that passphrase to reopen the imported database.
5. Open http://127.0.0.1:8765 (dashboard) or /welcome (landing page).
6. Stop any other CHW Compass server first (Control+C), or port 8765 will be occupied.

## What is imported
- `REGISTRE_DEMO`: 24 prelinked fictional patients, assigned numeric Compass IDs.
- Five `CLINIQUE_*` sheets: 100 original encounters preserving the 13 French columns *verbatim* in `imported_clinic_encounters.original_fields_json`.
- `LIENS_VISITES`: exact, fictional source-tab + row mapping; never infer identities from names or row numbers.
- `SUIVI_CHW`: 114 BP measurement records; selected explicit adherence responses only.
- `SMS_DEMO`: 96 messages imported *unclassified and needing human validation*. No automatic SMS is sent.
- `QUIZ_DEMO`: 72 historic quiz answers.

The existing dashboard shows imported patients, BP, adherence, messages and quizzes. Imported clinic history remains in an auxiliary table for future UI work and is not yet displayed in the patient panel. Historic imported messages are *not* automatically run through the classifier; to demonstrate AI interpretation, submit a new simulated message in the dashboard.

## Important limitations
- The original 13 clinic columns do not include BP or dates; BP is only imported from the separate fictional CHW follow-up tab.
- The current app UI and SMS classifier support Kiswahili and English only. Fictional French/Kirundi records are retained as text and **not** validated for local-language interpretation; `patients.language` uses Kiswahili as a *demo-interface fallback*, not a claim about patients' language preferences. Native-language review is necessary.
- Some adherence categories lose nuance in the existing binary schema; partial adherence is flagged as nonfully-adherent in the demo. This should become a three-state field before clinical use.
- No clinical improvement, stroke reduction or model performance can be inferred from synthetic records.
- The `private_data/` encrypted DB is separate from the default original demo. Never copy private databases or passphrases into Git.
- Real patient imports require consent, review of applicable privacy law, strong identity matching, clinician-approved thresholds, and more robust multi-user security.

## Rebuild synthetic JSON bundle
With artifact_tool installed: `python3 build_workbook_json.py CHW_Compass_Classeur_Clinique_Fictif.xlsx import_data/clinic_demo.json`.
