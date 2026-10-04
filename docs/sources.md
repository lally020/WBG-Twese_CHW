# Sources

Where our data, models and patient-facing content come from, and what they do not cover.

## Brief, models and tools

- Hack-Nation x World Bank brief, Small AI for Development, pages 7, 10, 11, 13, 14.
- Julia-1 model card, Supersonic Labs: https://huggingface.co/SupersonicLabs/Julia-1
- MASSIVE dataset, Amazon: listed in the brief, page 9.
- NLLB-200 and FLORES-200, Meta: listed in the brief, page 8.
- Gemma 3, Google, 2025: https://ollama.com/library/gemma3 (gemma3:4b primary, gemma3:1b fallback)
- Ollama: https://ollama.com
- Claude Code: https://docs.claude.com/en/docs/claude-code/overview

## Clinical guidelines

- Unger T, et al. 2020 International Society of Hypertension global hypertension practice guidelines. J Hypertens 2020. https://pmc.ncbi.nlm.nih.gov/articles/PMC8762770
- Dzudie A, et al. Roadmap to achieve 25% hypertension control in Africa by 2025. Cardiovasc J Afr 2017. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5642030/
- WHO CVD Risk Chart Working Group. World Health Organization cardiovascular disease risk charts: revised models to estimate risk in 21 global regions. Lancet Glob Health 2019. https://www.thelancet.com/journals/langlo/article/PIIS2214-109X(19)30318-3/fulltext
- Quiz sources cited per question in content/quiz/patient_htn.json: WHO HEARTS technical package (2018), ISH 2020 guidelines, WHO stroke warning signs (FAST), WHO hypertension fact sheet (2023).

The thresholds in config/guideline_htn.json are placeholders until the physician approves them ("approved": false).

## Patient-facing content (content/)

All of it is a draft. Nothing here is approved clinical content yet.

| File | Status |
|---|---|
| content/i18n/sms_templates.json | Template keys and English source. Drafted by Claude Code on 2026-10-03, awaiting review. |
| content/i18n/en.json | English source text. |
| content/i18n/sw.json | Swahili. Drafted by Claude Code, awaiting review by the bilingual teammate ("verified": false). |
| content/i18n/fr.json | French. Drafted by Claude Code, awaiting review by the bilingual teammate ("verified": false). |
| content/i18n/rn.json | Kirundi. Draft translation, not verified (see below). |
| content/i18n/sw_examples.json, fr_examples.json, en_examples.json | Example patient SMS used to build synthetic messages and labeled test sets. Written by Claude Code, not real patient messages. Swahili and French await review. |
| content/quiz/patient_htn.json | 15 patient education questions. Drafted by Claude Code for physician review ("approved": false). English is the source; Swahili, French and Kirundi are unverified. |

Kirundi: the current text is a draft, not verified by a speaker. Plan: regenerate it with NLLB-200 (run_Latn) from fr.json, then have a Kirundi speaker check it. Until then the engine reports "verified": false and the frontend shows "not yet verified".

The LLM in the engine never writes patient-facing text. All of the above is fixed text that people review.

## Data

Synthetic data only. data/make_synthetic.py creates 60 fictional active patients in 6 villages with BP series, visits, SMS, follow-ups, time logs and 8 events, plus 30 fictional former patients (the 6 months before, 18 of them ending in a stroke or admission) that feed only the peer-trajectory history, and writes synthetic = true into the meta table. data/make_labels.py builds the labeled test sets from the same templates. No real patient data is used.

What the synthetic data does not cover:

- Real blood pressure distributions in Burundi (levels, trends, how often readings are taken).
- Real SMS language: how patients actually write, spelling, code-switching, local terms.
- Real event rates (strokes, admissions) and who gets seen, so the risk score and peer-trajectory results show the mechanism, not real-world accuracy.
- Kirundi SMS: there are no Kirundi example messages yet.

None of the evaluation numbers are a clinical validation.
