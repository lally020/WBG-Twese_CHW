# First baseline findings

Synthetic development cases only; proposed labels are unreviewed. Rules mode, without a conversational LLM or LLM judge. No real texts were sent.

| Evaluation | Result |
|---|---|
| Classifier urgent detection | 3/15 expected urgent cases flagged (20% recall); 12 not flagged urgent, many abstentions |
| Full messaging urgent detection | 9/15 flagged (60% recall); 6 not flagged urgent |
| Full messaging urgent false alarms | 3/13 proposed nonurgent cases (negated, historical, quoted symptoms) |
| Main-system priority | 16/16 match the current demo policy specification |
| Conversation turn gates | 8/9 passed |
| Harness runtime errors | 0 |
| Evaluator unit tests | 6 passed |

The conversation failure is c02.2: after an urgent message, the human-review task remains urgent, but the next response's danger_flag is false/uncertain. The evaluator flags that response-contract inconsistency; it does not mean the urgent CHW task disappeared.

Next development priorities: have a clinician review proposed labels; investigate missed urgent paraphrases and self-harm/overdose/contextual messages; fix negation/history false alarms; make persistent urgent state consistent in response metadata. Rerun these unchanged development cases after each change. Separately build a locked, independently adjudicated holdout before making generalization or clinical claims.

A 100% nonempty-response rate does not establish conversational quality. The optional LLM evaluator has not been run; human review is still needed.
