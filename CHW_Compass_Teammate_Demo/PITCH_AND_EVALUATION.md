# World Bank Small AI hackathon — CHW Compass submission guide

## One-sentence problem statement
Because of CHW Compass, a patient with hypertension or diabetes can report an adherence obstacle through a familiar local-language SMS and receive structured follow-up from an existing CHW team, even when the clinic lacks reliable internet; our demonstration shows an offline NLP intent classifier, rule-based safety flags, and a supervised review workflow. **Clinical effectiveness has not been established.**

## 2–5 minute video structure
- 0:00–0:30: Explain the clinic's real CHW follow-up challenge; distinguish documented clinic experience from population-level evidence.
- 0:30–1:00: Show an ordinary phone as the patient-facing interface and explain that the model runs offline on a shared 8 GB clinic laptop; SMS still needs cellular service.
- 1:00–1:35: Demonstrate patient safety prioritization using severe BP and reported symptoms. *These are rules, not AI.*
- 1:35–2:15: Demonstrate English and Kiswahili incoming SMS and genuine tiny local intent classifier. Explain what AI adds over fixed SMS templates. Show low-confidence human review.
- 2:15–2:45: Schedule medication/visit/diet/lifestyle messages; require approval and simulate store-and-forward.
- 2:45–3:15: Quiz education, adherence log, historical BP chart.
- 3:15–3:45: Verified classifier correction; show same held-out evaluation V1 and V2; avoid claiming clinical efficacy.
- 3:45–4:15: Explicitly cover sources, synthetic data limits, privacy, realistic adoption path and human safeguards.

## Judging evidence to save
1. Video of app with Wi-Fi off, successful local NLP inference and quiz storage.
2. Screen of queue containing a message with `queued_offline`, then simulated delivery clearly labeled.
3. Side-by-side examples where a high historical BP is flagged by fixed clinical-review rule despite a low z-score.
4. Held-out 16-example results for intent classification, annotated as synthetic/nonclinical.
5. Audit record of supervisor reviewing a flagged SMS.
6. Local encrypted database file (do not share password or patient information).
7. `python -m pytest -q` test output.

## What not to claim
- No actual SMS is sent in this demo.
- No real stroke reduction or medication adherence effect has been measured.
- No clinician-approved local medical thresholds have been integrated.
- Model confidence is NOT clinical risk.
- The demo contains fabricated patients; it has no true prospectively collected safety data.
