# Video script (2 to 5 minutes)

The five parts from the brief (page 10), built around the step 13 demo. Record in one take, with WiFi off on screen. Before each take, run `POST /demo/reset` (or the reset button) so patient 1 starts low tier.

## 1. Problem (30 s)

"A community health worker in Burundi has about 20 hours a week and dozens of patients with high blood pressure. Some drift towards a stroke without anyone noticing. Some never text back."
Show the problem statement: "Because of this tool, a CHW in Burundi will spend this week's visits on the patients most likely to have a stroke, and catch rising blood pressure weeks earlier than they would otherwise; we know because [CITATION]."

## 2. The user and the setting (20 s)

Turn WiFi off on screen. "Everything runs on this laptop: the database, the small models, the screens. The model files are moved by USB."

## 3. Demo (2 to 3 min): follow docs/demo_script.json

1. The dashboard, with the yellow banner: "Thresholds not yet physician-approved."
2. On the phone, the patient texts in Swahili. Read the English gloss aloud. The inbox shows the text and the fields side by side. Confirm.
3. The flag and its reason: "11 mmHg above her own baseline, still rising." The tier moves to high.
4. Suggested action: "Visit this week." Override to recheck in 7 days, "patient travelling". Say: "This override is stored. It is feedback into the model."
5. The phone gets the danger-signs question. The reply is 2, which is wrong. Tap Send on the correction. Understanding drops.
6. The weekly plan. Patient 1 is now in the Kirundo visits. Change the hours from 20 to 8, and visits drop. Point at the escalation list: "Emergencies are never in the plan." Move one patient, then approve.
7. Supervisor: "Run monthly review." A new model version appears, with its held-out score. Say: "A person pressed this; the model never updates itself."
8. Switch the language to French, then Swahili. Kirundi shows "not yet verified".

## 4. How it works and why it is safe (40 s)

Small models only: Julia-1 (144M) picks from a fixed list, and Gemma 3 4B only copies fields into JSON and passes four checks. Rules come first for emergencies. "Not sure, ask a nurse" is always an option. People confirm every field, action and plan.

## 5. Evidence and limits (30 s)

Show the table from docs/evaluation.md. Say it plainly: the data is synthetic, and the labels come from our own rules. The physician sample is the only clinical check. This is not a clinical validation.
