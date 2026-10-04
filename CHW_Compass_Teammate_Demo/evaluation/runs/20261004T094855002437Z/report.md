# Compass evaluation report

**Synthetic development evaluation — not clinical validation.**

Mode: rules; model: not configured. Overall: FAIL

| Suite | Cases/turns | Score | Urgent precision | Urgent recall | F1 | Abstentions | p95 seconds | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| classifier | 32 | 0.375 | 1.000 | 0.200 | 0.333 | 20 | 0.001 | FAIL |
| messages | 32 | 0.719 | 0.750 | 0.600 | 0.667 | 11 | 0.004 | FAIL |
| main_system | 16 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 0.000 | PASS |
| conversations | 9 | 0.889 | 1.000 | 0.667 | 0.800 | 5 | 0.004 | FAIL |

## classifier confusion matrix

Rows = expected; columns = predicted.

| Expected / predicted | nonurgent | uncertain | urgent |
|---|---:|---:|---:|
| nonurgent | 5 | 8 | 0 |
| uncertain | 0 | 4 | 0 |
| urgent | 0 | 12 | 3 |

Urgent binary counts: TP 3; FP 0; FN 12; TN 13.
Positive = urgently flagged. Uncertain/error counts as not flagged; see abstentions and errors separately. TN is not medical clearance.

Failed cases: m01.1, m03.1, m04.1, m05.1, m07.1, m08.1, m09.1, m10.1, m11.1, m13.1, m14.1, m15.1, m17.1, m20.1, m21.1, m22.1, m23.1, m26.1, m27.1, m28.1

## messages confusion matrix

Rows = expected; columns = predicted.

| Expected / predicted | nonurgent | uncertain | urgent |
|---|---:|---:|---:|
| nonurgent | 5 | 5 | 3 |
| uncertain | 0 | 4 | 0 |
| urgent | 0 | 6 | 9 |

Urgent binary counts: TP 9; FP 3; FN 6; TN 10.
Positive = urgently flagged. Uncertain/error counts as not flagged; see abstentions and errors separately. TN is not medical clearance.

Failed cases: m05.1, m07.1, m08.1, m09.1, m10.1, m11.1, m21.1, m22.1, m23.1

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

| Expected / predicted | nonurgent | uncertain | urgent |
|---|---:|---:|---:|
| nonurgent | 2 | 4 | 0 |
| uncertain | 0 | 0 | 0 |
| urgent | 0 | 1 | 2 |

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
    "classifier": 0.0,
    "messages": 0.0,
    "main_system": 0.0,
    "conversations": 0.0
  },
  "new_failures": {
    "classifier": [],
    "messages": [],
    "main_system": [],
    "conversations": []
  },
  "regression_blocked": false
}
```