"""Step 6. Read one incoming SMS into fields, for the CHW to confirm.

1. A regex finds readings written like 140/90, "150 na 95", "150 sur 95".
2. Julia-1 (or the fallback intent model) names the intent: bp_report, symptom, meds_question, other.
3. llm.extract pulls sbp, dbp, meds_taken and symptoms, and runs the four checks.
4. If the regex and the LLM disagree on a number, the message goes to manual entry.
The fields are written to messages.parsed_json. An encounter row is written only by confirm_fields.

Usage:
    python engine/sms.py --db data/chw.db --config config/guideline_htn.json --message-id 12
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import llm, models
from engine.common import audit, connect, dumps, load_config, loads, now_iso

BP_PATTERN = re.compile(
    # The lookarounds skip dates like 2026-10-03: a number touching another '-' or '/' is not a reading.
    r"(?<![\d/-])\b(\d{2,3})\s*(?:/|\\|-|over|na|kwa|sur|et|and)\s*(\d{2,3})\b(?![/-]\d)", re.IGNORECASE
)


def regex_bp(text):
    """The first reading in the text as (sbp, dbp), or None."""
    m = BP_PATTERN.search(text or "")
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return (a, b) if a >= b else (b, a)


def process(con, cfg, message_id):
    """Parse one incoming message and store the result in parsed_json. Returns the parsed dict."""
    msg = con.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if msg is None:
        return {"error": f"message {message_id} not found"}
    if msg["direction"] != "in":
        return {"error": "only incoming messages are processed"}
    text = msg["text"]

    rx = regex_bp(text)
    intent, intent_probs, intent_backend = models.classify_intent(cfg, text, has_numbers=rx is not None)
    ext = llm.extract(cfg, text, con=con, message_id=message_id)

    fields, needs_manual, reason = None, False, ext["reason"]
    if ext["valid"]:
        fields = ext["fields"]
        if rx and (fields["sbp"], fields["dbp"]) != rx:
            needs_manual = True
            reason = (f"The regex read {rx[0]}/{rx[1]} but the LLM read {fields['sbp']}/{fields['dbp']}. "
                      "Manual entry.")
    elif rx:
        # The LLM failed: keep the clean numbers from the regex, the CHW fills the rest.
        fields = {"sbp": rx[0], "dbp": rx[1], "meds_taken": None, "symptoms": [], "free_text_rest": ""}
        needs_manual = True
        reason = f"LLM check failed ({ext['reason']}). Numbers from the regex; check the rest by hand."
    else:
        needs_manual = True
        reason = f"LLM check failed ({ext['reason']}). Manual entry."

    emergency_codes = set(cfg["symptoms_emergency"])
    possible_emergency = bool(fields and emergency_codes & set(fields.get("symptoms") or []))
    parsed = {
        "intent": intent,
        "intent_probs": intent_probs,
        "intent_backend": intent_backend,
        "regex_bp": list(rx) if rx else None,
        "llm": {"valid": ext["valid"], "reason": ext["reason"], "model": ext["model"]},
        "fields": fields,
        "valid": ext["valid"] and not needs_manual,
        "needs_manual": needs_manual,
        "possible_emergency": possible_emergency,
        "reason": reason,
        "processed_at": now_iso(),
        "confirmed": False,
    }
    kind = "report" if intent in ("bp_report", "symptom") else "other"
    con.execute("UPDATE messages SET parsed_json = ?, kind = ? WHERE id = ?", (dumps(parsed), kind, message_id))
    con.commit()
    return {"message_id": message_id, "text": text, **parsed}


def confirm(con, cfg, message_id, fields):
    """The CHW confirmed (or corrected) the fields. Write the encounter row. Returns its id or None."""
    msg = con.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if msg is None:
        return {"error": f"message {message_id} not found"}
    if msg["encounter_id"]:
        return {"error": "message already confirmed", "encounter_id": msg["encounter_id"]}
    fields = fields or {}
    sbp, dbp = fields.get("sbp"), fields.get("dbp")
    meds = fields.get("meds_taken")
    symptoms = [s for s in (fields.get("symptoms") or []) if s and s != "none"]
    if meds not in (None, "yes", "no", "partial"):
        return {"error": "meds_taken must be yes, no, partial or empty"}
    bad = [s for s in symptoms if s not in cfg["llm"]["symptom_codes"]]
    if bad:
        return {"error": f"unknown symptom codes {bad}"}

    parsed = loads(msg["parsed_json"], {}) or {}
    parsed.update(confirmed=True, confirmed_fields=fields, confirmed_at=now_iso())
    encounter_id = None
    if sbp is not None or meds is not None or symptoms:
        bp_text = f"{sbp}/{dbp}" if sbp is not None and dbp is not None else (str(sbp) if sbp is not None else None)
        codes = fields.get("context_codes")
        if codes is None:
            codes = ((parsed.get("fields") or {}).get("context_codes")) or []
        cur = con.execute(
            "INSERT INTO encounters (patient_id, date, source, complaint_text, bp_text, sbp, dbp, meds_taken, "
            "symptom_codes, lang, context_codes_json) VALUES (?, ?, 'sms', ?, ?, ?, ?, ?, ?, ?, ?)",
            (msg["patient_id"], msg["received_at"][:10], msg["text"], bp_text, sbp, dbp, meds,
             dumps(symptoms), msg["lang"], dumps(codes)),
        )
        encounter_id = cur.lastrowid
        audit(con, "insert", "encounters", encounter_id)
    con.execute("UPDATE messages SET parsed_json = ?, encounter_id = ? WHERE id = ?",
                (dumps(parsed), encounter_id, message_id))
    audit(con, "confirm_fields", "messages", message_id)
    con.commit()
    return {"message_id": message_id, "patient_id": msg["patient_id"], "encounter_id": encounter_id,
            "intent": parsed.get("intent"), "fields": fields}


def main():
    parser = argparse.ArgumentParser(description="Parse one incoming SMS.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--message-id", type=int, required=True)
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    print(json.dumps(process(con, cfg, args.message_id), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
