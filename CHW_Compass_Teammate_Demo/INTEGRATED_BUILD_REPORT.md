# Pain-history verification

55 tests passed in 50.67 seconds (one dependency deprecation warning). Nine additional pain cases check complete intake and review summary, skipping explicit details, invalid severity, three initial urgent blocks, mid-interview breathing warning, encrypted restart/cancellation, and urgent BP override. Python compilation and both frontend JavaScript syntax checks passed. A separate six-message knee-pain demonstration completed with the actual experimental classifier (all six outputs were uncertain, not clinical clearance) and no LLM call. This does not establish clinical safety or real-world model quality. See PAIN_HISTORY_START_HERE.md.

Earlier reports below are historical.

# Open-ended update verification

46 tests passed in 45.77 seconds (one dependency deprecation warning). New checks cover multi-turn generated reply persistence/history, no forced quiz, separate urgency classification, classifier-triggered dashboard priority, preservation of unresolved urgent tasks, withholding a sample generated treatment instruction, language abstention, and fallback persistence. Local-model outputs are mocked in these tests. Real model response quality, classifier clinical accuracy, and speed on the Mac remain unvalidated. The separate classifier was trained on 40 synthetic English examples. See OPEN_ENDED_SMS_START_HERE.md.

Earlier reports below are retained as historical records.

# Local-model update verification

41 tests passed in 34.34 seconds (one dependency deprecation warning). This includes the original 27 checks plus 14 local-model protocol, fallback, controller, urgency, contextual-reply and food-routing cases. Model outputs in those tests were mocked; real model inference was not run here. Read LOCAL_MODEL_START_HERE.md and use the new local-model launcher to run a real smoke test on your Mac.

# Integrated build verification — October 4, 2026 UTC

This is a consolidated replacement of the uploaded application. No existing private database or bundled Mac virtual environment is included. Original code, frontend, demo import, and original tests are retained with the fixes below.

## Verified result

27 tests passed in 33.36 seconds, including the original 14 tests and 13 added integration cases. One dependency deprecation warning was emitted. Python compilation and dashboard/agent-demo JavaScript syntax checks passed.

| Patient reply | Tested behavior |
| --- | --- |
| I took my pills this morning | Record self-report; send education quiz |
| I forgot again | Record missed self-report; ask why |
| I ran out of medicine | Persist acknowledgment; create CHW handoff |
| The medicine makes me dizzy | Persist acknowledgment; clinical review |
| Can someone visit me tomorrow? | Acknowledge request; CHW confirms arrangements |
| My chest hurts and I can't breathe | Danger flag; urgent human-review task; visible reply |
| I don't understand your question | Repeat the current approved question |
| xyz 123 ??? | Acknowledge; human review |
| I took my medicine but did not take all my pills | Missed-dose interpretation takes precedence |

Additional checks: messages after human handoff and after stopping enrollment still receive a persisted response; enrollment stays stopped. API checks cover enrollment, three free-text replies, visible message history, review queue, summary, patient pagination, static pages, blank-message rejection, and encrypted restart recovery. Concurrent bounded scheduler calls do not select the same due patient in the tested single-process application. An injected reply failure rolls back inbound, outbound, and state changes together.

## What changed

Agent reply handling saves an outbound response for every accepted SMS, including unknown replies and non-enrolled inbound contacts. A limited phrase interpretation layer adds English free-text examples above; original English/Kiswahili menus remain. Original patient text is retained. Danger rules include chest-hurts and breathing variants, including can't/can’t. These are conservative phrase flags, not medical understanding.

The scheduler processes at most 100 due patients per default tick (maximum 500), using an index and one transaction per tick. Server shutdown stops its scheduler thread. The phone demo loads a bounded first page and retains typed text on failed submission.

## Honest limits

This is a local, single-process synthetic demo with simulated SMS. It uses a small offline classifier plus limited phrase rules and approved replies. No general-purpose LLM or MedGemma is integrated. Every accepted message receives a response; that does not mean every message is understood. Unknown messages go to humans. English phrase examples are tested; equivalent open-ended Kiswahili/French coverage is not established. The full encrypted snapshot is still rewritten per transaction. File-save failures and power loss are not validated as production durability. Multi-process workers and real SMS delivery are not supported or tested.

No new 150,000-patient benchmark was run. This report establishes the listed functional workflows, not population-scale readiness. Pages were checked through API/static responses and JavaScript syntax, not a rendered browser walkthrough. No testing was performed on your Mac.

## Run the consolidated application

1. Keep your existing project folder as a backup. Extract this ZIP into a fresh folder.
2. Use Python 3.11 or newer. Run `bash run_mac.command` from the extracted project folder, or create a virtual environment, install requirements.txt, and run `python launch.py`.
3. Choose a 12+ character passphrase. Open http://127.0.0.1:8765/agent-demo.
4. The demo selects a routine fictional patient. Click Enable fictional consent + agent.
5. Send `I forgot again`, then `I ran out of medicine`. Both replies should appear in the phone thread. The conversation should become awaiting human.
6. Open CHW Dashboard → Human approvals. Find the medication-access review task.
7. To test another branch, select another routine patient and enroll them. Send `I took my pills this morning`, then `A`.
8. On another fictional patient, test the danger example and inspect the urgent review task.

For automated verification: install requirements-dev.txt, then run `PYTHONPATH=. python -m pytest -q tests` from the project folder.

If retaining an existing synthetic database, stop the old server first and copy only its encrypted database into the fresh folder's private_data directory; use its original passphrase. Existing unresolved human-review conversations remain unresolved. Re-enrollment resets the demo conversation; do not use it to dismiss genuine clinical concerns.
