"""Step 10. Patient education questions by SMS ("Reply 1, 2 or 3").

- next_question picks the question that is due first (never asked comes first), rotating topics,
  in the patient's language, and queues it as an outgoing SMS.
- record_answer maps a reply of 1, 2 or 3 to an option; other text goes to Julia-1 (or a simple
  word match when Julia-1 is not installed), which may only pick an option or 'unclear'.
  A wrong answer queues the approved correction for the CHW to send with one tap.
- understanding gives the share correct per topic over config.quiz.window_days.
- support_needed lists patients and topics below config.quiz.low_understanding_below.
Questions and corrections come from content/quiz/patient_htn.json, written by people.

Usage:
    python engine/quiz.py --db data/chw.db --config config/guideline_htn.json --patient 1 [--answer "2"]
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import content, models
from engine.common import add_days, audit, connect, dumps, iso, load_config, now_iso


def _lang(con, patient_id):
    row = con.execute("SELECT language FROM patients WHERE id = ?", (patient_id,)).fetchone()
    return row["language"] if row else "en"


def _pick(qtext, lang):
    """Text in the patient's language, English when missing. Returns (text, lang used)."""
    if qtext.get(lang):
        return qtext[lang], lang
    return qtext["en"], "en"


def format_sms(q, lang):
    text, used = _pick(q["text"], lang)
    opts, _ = _pick(q["options"], used)
    reply, _, _ = content.sms_text("quiz_reply_instruction", used)
    lines = [text] + [f"{i}) {o}" for i, o in enumerate(opts, 1)] + [reply]
    return "\n".join(lines), used


def pending(con, patient_id):
    """The question asked and not yet answered, if any."""
    row = con.execute("SELECT * FROM quiz_results WHERE patient_id = ? AND answer IS NULL "
                      "ORDER BY asked_at DESC, id DESC LIMIT 1", (patient_id,)).fetchone()
    return dict(row) if row else None


def next_question(con, cfg, patient_id, channel="sms", as_of=None):
    """Pick the next question, store it as asked, and (for SMS) queue it. Returns the question."""
    p = pending(con, patient_id)
    if p:
        q = content.quiz_question(p["question_id"])
        text, used = format_sms(q, p["lang"] or _lang(con, patient_id))
        return {"patient_id": patient_id, "question_id": q["id"], "topic": q["topic"], "text": text,
                "lang": used, "already_pending": True, "quiz_result_id": p["id"]}
    bank = content.quiz_bank()["questions"]
    topics = cfg["quiz"]["topics"]
    last = {}
    for r in con.execute("SELECT question_id, next_due, topic, asked_at FROM quiz_results WHERE patient_id = ? "
                         "ORDER BY asked_at", (patient_id,)).fetchall():
        last[r["question_id"]] = r
    last_topic = con.execute("SELECT topic FROM quiz_results WHERE patient_id = ? ORDER BY asked_at DESC LIMIT 1",
                             (patient_id,)).fetchone()
    last_topic = last_topic["topic"] if last_topic else None

    today = iso(as_of)

    def key(q):
        # Never asked counts as due today; overdue questions come before it, later ones after it.
        due = (last[q["id"]]["next_due"] if q["id"] in last else None) or today
        return (due, q["topic"] == last_topic, topics.index(q["topic"]) if q["topic"] in topics else 99, q["id"])

    q = sorted(bank, key=key)[0]
    lang = _lang(con, patient_id)
    text, used = format_sms(q, lang)
    when = now_iso() if as_of is None else f"{iso(as_of)}T12:00:00"
    cur = con.execute(
        "INSERT INTO quiz_results (patient_id, question_id, topic, asked_at, channel, lang) VALUES (?, ?, ?, ?, ?, ?)",
        (patient_id, q["id"], q["topic"], when, channel, used),
    )
    rid = cur.lastrowid
    msg_id = None
    if channel == "sms":
        m = con.execute(
            "INSERT INTO messages (patient_id, direction, kind, lang, text, received_at, parsed_json, status) "
            "VALUES (?, 'out', 'quiz', ?, ?, ?, ?, 'queued')",
            (patient_id, used, text, when, dumps({"question_id": q["id"], "quiz_result_id": rid})),
        )
        msg_id = m.lastrowid
    con.commit()
    return {"patient_id": patient_id, "question_id": q["id"], "topic": q["topic"], "text": text, "lang": used,
            "channel": channel, "message_id": msg_id, "quiz_result_id": rid, "already_pending": False}


def _match_option(cfg, q, lang, reply):
    """Map a reply to option 1, 2, 3 or None (unclear)."""
    m = re.match(r"^\s*([123])\b", reply)
    if m:
        return int(m.group(1)), "digit"
    opts, _ = _pick(q["options"], lang)
    choices = {str(i): o for i, o in enumerate(opts, 1)}
    choices["unclear"] = "The reply does not match any option"
    out = models.julia_choose(cfg, reply, "Which option does this reply choose?", choices) \
        if models.julia_on(cfg, "quiz_reply") else None
    if out:
        c = out[0]
        return (int(c) if c != "unclear" else None), "julia-1"
    # No Julia-1: a reply that contains most words of exactly one option.
    words = set(re.findall(r"\w+", reply.lower()))
    hits = []
    for i, o in enumerate(opts, 1):
        ow = set(re.findall(r"\w+", o.lower()))
        if ow and len(words & ow) / len(ow) >= 0.6:
            hits.append(i)
    return (hits[0] if len(hits) == 1 else None), "word-match"


def record_answer(con, cfg, patient_id, question_id, reply_text, as_of=None):
    """Store the answer, set next_due, and queue the correction on a wrong answer."""
    q = content.quiz_question(question_id)
    if q is None:
        return {"error": f"question {question_id} not found"}
    row = con.execute("SELECT * FROM quiz_results WHERE patient_id = ? AND question_id = ? AND answer IS NULL "
                      "ORDER BY asked_at DESC LIMIT 1", (patient_id, question_id)).fetchone()
    lang = (row["lang"] if row else None) or _lang(con, patient_id)
    option, how = _match_option(cfg, q, lang, reply_text or "")
    if option is None:
        correct = "unclear"
    else:
        correct = "yes" if option == q["correct"] else "no"
    today = iso(as_of)
    days = cfg["quiz"]["repeat_days_right"] if correct == "yes" else cfg["quiz"]["repeat_days_wrong"]
    next_due = add_days(today, days)
    if row:
        rid = row["id"]
        con.execute("UPDATE quiz_results SET answer = ?, correct = ?, next_due = ? WHERE id = ?",
                    (reply_text, correct, next_due, rid))
    else:
        rid = con.execute(
            "INSERT INTO quiz_results (patient_id, question_id, topic, asked_at, channel, answer, correct, lang, next_due) "
            "VALUES (?, ?, ?, ?, 'sms', ?, ?, ?, ?)",
            (patient_id, question_id, q["topic"], now_iso(), reply_text, correct, lang, next_due),
        ).lastrowid
    correction_id = None
    if correct == "no":
        text, used = _pick(q["correction"], lang)
        correction_id = con.execute(
            "INSERT INTO messages (patient_id, direction, kind, lang, text, received_at, parsed_json, status) "
            "VALUES (?, 'out', 'correction', ?, ?, ?, ?, 'queued')",
            (patient_id, used, text, now_iso(), dumps({"question_id": question_id, "quiz_result_id": rid})),
        ).lastrowid
    audit(con, "record_answer", "quiz_results", rid)
    con.commit()
    return {"patient_id": patient_id, "question_id": question_id, "topic": q["topic"], "reply": reply_text,
            "option": option, "matched_by": how, "correct": correct, "next_due": next_due,
            "correction_message_id": correction_id, "understanding": understanding(con, cfg, patient_id, as_of)}


def understanding(con, cfg, patient_id, as_of=None):
    from engine.decide import _understanding
    return _understanding(con, cfg, patient_id, iso(as_of))


def support_needed(con, cfg, as_of=None):
    """Patients and topics below the understanding cut-off, for a chart in the frontend."""
    cut = cfg["quiz"]["low_understanding_below"]
    out = []
    for (pid,) in con.execute("SELECT DISTINCT patient_id FROM quiz_results ORDER BY patient_id").fetchall():
        u = understanding(con, cfg, pid, as_of)
        low = {t: v for t, v in u.items() if v is not None and v < cut}
        if low:
            out.append({"patient_id": pid, "low_topics": low})
    by_topic = {t: sum(t in r["low_topics"] for r in out) for t in cfg["quiz"]["topics"]}
    return {"cutoff": cut, "patients": out, "count_by_topic": by_topic}


def main():
    parser = argparse.ArgumentParser(description="Ask the next education question, or record an answer.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--patient", type=int, required=True)
    parser.add_argument("--answer", default=None, help="record this reply to the pending question")
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    if args.answer is None:
        print(json.dumps(next_question(con, cfg, args.patient), indent=2, ensure_ascii=False))
    else:
        p = pending(con, args.patient)
        if not p:
            print("No pending question for this patient.")
            return
        print(json.dumps(record_answer(con, cfg, args.patient, p["question_id"], args.answer), indent=2,
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
