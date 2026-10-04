# Compass evaluation report

**Synthetic development evaluation — not clinical validation.**

Mode: rules; model: not configured. Overall: FAIL

| Suite | Cases/turns | Score | Urgent precision | Urgent recall | F1 | Abstentions | p95 seconds | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| classifier | 32 | 0.938 | 1.000 | 0.933 | 0.966 | 1 | 0.001 | PASS |
| messages | 32 | 0.906 | 0.833 | 1.000 | 0.909 | 0 | 0.006 | PASS |
| main_system | 16 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 0.000 | PASS |
| conversations | 9 | 0.889 | 1.000 | 0.667 | 0.800 | 0 | 0.007 | FAIL |

## classifier confusion matrix

Rows = expected; columns = predicted.

| Expected / predicted | nonurgent | uncertain | urgent |
|---|---:|---:|---:|
| nonurgent | 13 | 0 | 0 |
| uncertain | 1 | 3 | 0 |
| urgent | 0 | 1 | 14 |

Urgent binary counts: TP 14; FP 0; FN 1; TN 13.
Positive = urgently flagged. Uncertain/error counts as not flagged; see abstentions and errors separately. TN is not medical clearance.

Failed cases: m15.1, m32.1

## messages confusion matrix

Rows = expected; columns = predicted.

| Expected / predicted | nonurgent | uncertain | urgent |
|---|---:|---:|---:|
| nonurgent | 10 | 0 | 3 |
| uncertain | 1 | 3 | 0 |
| urgent | 0 | 0 | 15 |

Urgent binary counts: TP 15; FP 3; FN 0; TN 10.
Positive = urgently flagged. Uncertain/error counts as not flagged; see abstentions and errors separately. TN is not medical clearance.

Failed cases: m21.1, m22.1, m23.1

## main_system confusion matrix

Rows = expected; columns = predicted.

| Expected / predicted | followup | routine | urgent |
|---|---:|---:|---:|
| followup | 8 | 0 | 0 |
| routine | 0 | 3 | 0 |
| urgent | 0 | 0 | 5 |

Urgent binary counts: TP 5; FP 0; FN 0; TN 11.
Positive = urgently flagged. Uncertain/error counts as not flagged; see abstentions and errors separately. TN is not medical clearance.

Failed cases: none

## conversations confusion matrix

Rows = expected; columns = predicted.

| Expected / predicted | nonurgent | urgent |
|---|---:|---:|
| nonurgent | 6 | 0 |
| urgent | 1 | 2 |

Urgent binary counts: TP 2; FP 0; FN 1; TN 6.
Positive = urgently flagged. Uncertain/error counts as not flagged; see abstentions and errors separately. TN is not medical clearance.

Failed cases: c02.2

## Limits

- Development cases can be reused in candidate training; post-training development scores are not held-out performance.
- Synthetic development cases; not clinician-reviewed or clinical validation.
- Main-system targets test existing demo policy, not clinical correctness.
- No carrier/network delivery timing; only local processing.
- Abstentions and errors are not safety clearance.
- Age/sex/pregnancy are only tested when mentioned in text; structured context is not implemented.

## Baseline comparison

```json
{
  "comparable": true,
  "baseline": "/Users/arianne/Downloads/CHW_Compass_Teammate_Demo/evaluation/baseline_rules/report.json",
  "note": "Same dataset/config required. Different hardware can invalidate timing comparisons. No automatic promotion.",
  "score_deltas": {
    "classifier": 0.5625,
    "messages": 0.1875,
    "main_system": 0.0,
    "conversations": 0.0
  },
  "new_failures": {
    "classifier": [
      "m32.1"
    ],
    "messages": [],
    "main_system": [],
    "conversations": []
  },
  "regression_blocked": true
}
```