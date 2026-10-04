"""Step 7. Follow-ups and reminders.

- List due, missed and done follow-ups, including clinic appointments.
- Queue a reminder SMS config.reminders.days_before_appointment days before an appointment.
- Queue a follow-up SMS config.reminders.follow_up_after_visit_days days after a CHW visit.
Both use content/i18n templates in the patient's language and wait as 'queued' until mark_sent.

Usage:
    python engine/followups.py --db data/chw.db --config config/guideline_htn.json [--date 2026-10-03] [--queue]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import content
from engine.common import add_days, audit, connect, dumps, iso, load_config, loads, now_iso


def update_missed(con, as_of=None):
    """Missed means due_date is past and done_on is empty."""
    con.execute("UPDATE followups SET status = 'missed' WHERE status = 'due' AND done_on IS NULL AND due_date < ?",
                (iso(as_of),))
    con.commit()


def list_followups(con, patient_id=None, as_of=None):
    update_missed(con, as_of)
    sql = ("SELECT f.*, p.village FROM followups f JOIN patients p ON p.id = f.patient_id "
           + ("WHERE f.patient_id = ? " if patient_id else "") + "ORDER BY f.due_date")
    rows = [dict(r) for r in con.execute(sql, (patient_id,) if patient_id else ()).fetchall()]
    return {s: [r for r in rows if r["status"] == s] for s in ("due", "missed", "done")}


def mark_done(con, patient_id, kind, on_date=None):
    """Mark the earliest open follow-up of this kind as done (for example after a CHW visit)."""
    row = con.execute(
        "SELECT id FROM followups WHERE patient_id = ? AND kind = ? AND done_on IS NULL "
        "ORDER BY due_date LIMIT 1", (patient_id, kind)
    ).fetchone()
    if row:
        con.execute("UPDATE followups SET status = 'done', done_on = ? WHERE id = ?", (iso(on_date), row["id"]))
        con.commit()
        return row["id"]
    return None


def queue_sms(con, patient_id, kind, key, extra=None, **values):
    """Put one templated SMS in the outgoing queue. Returns the message id."""
    lang = con.execute("SELECT language FROM patients WHERE id = ?", (patient_id,)).fetchone()["language"]
    text, used_lang, verified = content.sms_text(key, lang, **values)
    parsed = {"template": key, "verified": verified, **(extra or {})}
    cur = con.execute(
        "INSERT INTO messages (patient_id, direction, kind, lang, text, received_at, parsed_json, status) "
        "VALUES (?, 'out', ?, ?, ?, ?, ?, 'queued')",
        (patient_id, kind, used_lang, text, now_iso(), dumps(parsed)),
    )
    audit(con, "queue_sms", "messages", cur.lastrowid, user_id="engine")
    return cur.lastrowid


def _already_queued(con, patient_id, kind, ref_key, ref_value):
    for (pj,) in con.execute("SELECT parsed_json FROM messages WHERE patient_id = ? AND direction = 'out' AND kind = ?",
                             (patient_id, kind)).fetchall():
        if (loads(pj, {}) or {}).get(ref_key) == ref_value:
            return True
    return False


def queue_reminders(con, cfg, as_of=None):
    """Queue appointment reminders, post-visit follow-ups and the weekly borderline asks that are due today.
    Returns the new message ids."""
    as_of = iso(as_of)
    r = cfg["reminders"]
    new = []
    # Appointments within the next N days.
    limit = add_days(as_of, r["days_before_appointment"])
    for f in con.execute(
        "SELECT * FROM followups WHERE kind = 'clinic_appointment' AND status = 'due' AND due_date BETWEEN ? AND ?",
        (as_of, limit),
    ).fetchall():
        if not _already_queued(con, f["patient_id"], "reminder", "followup_id", f["id"]):
            new.append(queue_sms(con, f["patient_id"], "reminder", "reminder_appointment",
                                 extra={"followup_id": f["id"]}, date=f["due_date"]))
    # CHW visits N days ago (look back two windows, so a missed day still catches up).
    since = add_days(as_of, -2 * r["follow_up_after_visit_days"])
    until = add_days(as_of, -r["follow_up_after_visit_days"])
    for e in con.execute(
        "SELECT id, patient_id FROM encounters WHERE source = 'chw' AND date BETWEEN ? AND ?", (since, until + "T99")
    ).fetchall():
        if not _already_queued(con, e["patient_id"], "follow_up", "encounter_id", e["id"]):
            new.append(queue_sms(con, e["patient_id"], "follow_up", "follow_up_after_visit",
                                 extra={"encounter_id": e["id"]}))
    con.commit()
    return new + queue_borderline_asks(con, cfg, as_of)


def create_after_decision(con, cfg, patient_id, final_choice, urgent=False, as_of=None):
    """Create every follow-up in config.followup_after_decision[final_choice]. Called by decide.py once the
    CHW's choice is stored. days_if_urgent replaces days when the flag behind the decision carried urgent_days;
    days_after_appointment counts from the clinic appointment created just before it."""
    created = []
    appointment_due = None
    for item in cfg.get("followup_after_decision", {}).get(final_choice, []):
        if "days_after_appointment" in item:
            if appointment_due is None:
                continue
            due = add_days(appointment_due, item["days_after_appointment"])
        else:
            days = item["days_if_urgent"] if urgent and "days_if_urgent" in item else item["days"]
            due = add_days(as_of, days)
        cur = con.execute("INSERT INTO followups (patient_id, due_date, kind, status) VALUES (?, ?, ?, 'due')",
                          (patient_id, due, item["kind"]))
        if item["kind"] == "clinic_appointment":
            appointment_due = due
        audit(con, "create_followup", "followups", cur.lastrowid, user_id="engine")
        created.append({"id": cur.lastrowid, "kind": item["kind"], "due_date": due,
                        **({"purpose": item["purpose"]} if item.get("purpose") else {})})
    con.commit()
    return created


# ---------- the borderline watch (PLAN.md step 3, ask_weekly_sms) ----------

def _watch_key(patient_id):
    return f"borderline_watch:{patient_id}"


def borderline_watch(con, cfg, patient_id, flags, as_of=None):
    """Start or stop the weekly BP-request SMS for a borderline patient. One open watch per patient (so a weekly
    recomputation never asks twice). It stops after two readings in a row below the band; a reading above the goal
    does not stop it. Returns "started", "stopped" or None."""
    from engine import signals
    from engine.common import bp_series, get_meta
    b = cfg.get("borderline", {})
    if not b.get("ask_weekly_sms"):
        return None
    open_id = get_meta(con, _watch_key(patient_id))
    readings = bp_series(con, patient_id, as_of)
    goal = signals.patient_goal(cfg, con.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone())
    s_lo, d_lo = signals.band_floor(cfg, goal)
    last2 = readings[-2:]
    clear = len(last2) == 2 and all(r["sbp"] < s_lo and (r["dbp"] is None or r["dbp"] < d_lo) for r in last2)
    if open_id and clear:
        con.execute("UPDATE followups SET status = 'done', done_on = ? WHERE id = ? AND done_on IS NULL",
                    (iso(as_of), int(open_id)))
        con.execute("DELETE FROM meta WHERE key = ?", (_watch_key(patient_id),))
        con.commit()
        return "stopped"
    if not open_id and any(f["type"] == "borderline" for f in flags):
        fid = con.execute("INSERT INTO followups (patient_id, due_date, kind, status) VALUES (?, ?, 'sms_check', 'due')",
                          (patient_id, add_days(as_of, 7))).lastrowid
        con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (_watch_key(patient_id), str(fid)))
        audit(con, "borderline_watch_start", "followups", fid, user_id="engine")
        con.commit()
        return "started"
    return None


def queue_borderline_asks(con, cfg, as_of=None):
    """Each week: for every open borderline watch that is due, queue the BP-request SMS and book the next week."""
    as_of = iso(as_of)
    new = []
    for key, fid in con.execute("SELECT key, value FROM meta WHERE key LIKE 'borderline_watch:%'").fetchall():
        pid = int(key.split(":")[1])
        f = con.execute("SELECT * FROM followups WHERE id = ?", (int(fid),)).fetchone()
        if f is None:
            continue
        if f["done_on"]:  # the patient already sent a reading this week: book the next ask a week after it
            nxt = con.execute("INSERT INTO followups (patient_id, due_date, kind, status) VALUES (?, ?, 'sms_check', 'due')",
                              (pid, add_days(f["done_on"], 7))).lastrowid
            con.execute("UPDATE meta SET value = ? WHERE key = ?", (str(nxt), key))
            continue
        if f["due_date"] > as_of:
            continue
        new.append(queue_sms(con, pid, "follow_up", "sms_check", extra={"followup_id": f["id"], "purpose": "borderline_watch"}))
        con.execute("UPDATE followups SET status = 'done', done_on = ? WHERE id = ?", (as_of, f["id"]))
        nxt = con.execute("INSERT INTO followups (patient_id, due_date, kind, status) VALUES (?, ?, 'sms_check', 'due')",
                          (pid, add_days(as_of, 7))).lastrowid
        con.execute("UPDATE meta SET value = ? WHERE key = ?", (str(nxt), key))
    con.commit()
    return new


def outgoing_queue(con):
    rows = con.execute(
        "SELECT m.*, p.village FROM messages m JOIN patients p ON p.id = m.patient_id "
        "WHERE m.direction = 'out' AND m.status = 'queued' ORDER BY m.received_at, m.id"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["parsed_json"] = loads(d["parsed_json"], {})
        out.append(d)
    return out


def mark_sent(con, message_id):
    row = con.execute("SELECT direction, status FROM messages WHERE id = ?", (message_id,)).fetchone()
    if row is None:
        return {"error": f"message {message_id} not found"}
    if row["direction"] != "out":
        return {"error": "only outgoing messages can be marked sent"}
    con.execute("UPDATE messages SET status = 'sent' WHERE id = ?", (message_id,))
    audit(con, "mark_sent", "messages", message_id)
    con.commit()
    return {"message_id": message_id, "status": "sent"}


def main():
    parser = argparse.ArgumentParser(description="List follow-ups and queue reminders.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--date", default=None)
    parser.add_argument("--queue", action="store_true", help="queue reminders and follow-up messages")
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    lists = list_followups(con, as_of=args.date)
    print({k: len(v) for k, v in lists.items()})
    if args.queue:
        ids = queue_reminders(con, cfg, args.date)
        print(f"Queued {len(ids)} messages.")
    for m in outgoing_queue(con)[:10]:
        print(f"  #{m['id']} to patient {m['patient_id']} ({m['lang']}, {m['kind']}): {m['text'][:80]}")


if __name__ == "__main__":
    main()
