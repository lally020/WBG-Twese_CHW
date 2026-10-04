"""The engine's functions for the React frontend. Each returns plain JSON-serializable data.
engine/server.py exposes every function as an HTTP route on 127.0.0.1:8000.

The database and config paths come from the environment, with the repo defaults:
    TWESE_DB=data/chw.db  TWESE_CONFIG=config/guideline_htn.json
"""

import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import content, followups as fu, hours as hrs, llm, models, quiz as qz, signals
from engine import decide as dec, plan_week as pw, risk as rk, trajectory as traj
from engine.common import (ROOT, audit, bp_series, connect, dumps, get_meta, iso, last_contact_date,
                           load_config, loads, now_iso, root_path)

DB = os.environ.get("TWESE_DB", "data/chw.db")
CONFIG = os.environ.get("TWESE_CONFIG", "config/guideline_htn.json")


def configure(db=None, config=None):
    """Point the API at another database or config (used by the tests)."""
    global DB, CONFIG
    DB = db or DB
    CONFIG = config or CONFIG


def cfg():
    return load_config(CONFIG)


_schema_checked = set()


def ensure_schema(con):
    """Bring an older chw.db up to date without losing data: add patients.goal_override and the users table
    when they are missing (SQLite cannot say ADD COLUMN IF NOT EXISTS, so check first)."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(patients)")}
    ecols = {r[1] for r in con.execute("PRAGMA table_info(encounters)")}
    if ecols and "context_codes_json" not in ecols:
        con.execute("ALTER TABLE encounters ADD COLUMN context_codes_json TEXT")
    # Older databases: the flags table must accept the borderline type (SQLite cannot change a CHECK in place).
    fsql = (con.execute("SELECT sql FROM sqlite_master WHERE name = 'flags'").fetchone() or [""])[0] or ""
    if fsql and "borderline" not in fsql:
        con.executescript(
            "ALTER TABLE flags RENAME TO flags_old;"
            "CREATE TABLE flags (id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), "
            "date TEXT NOT NULL, type TEXT CHECK (type IN ('threshold', 'trend', 'symptom', 'who_risk', 'borderline')), "
            "value TEXT, reason_text TEXT, guideline_ref TEXT, emergency TEXT CHECK (emergency IN ('yes', 'no')));"
            "INSERT INTO flags SELECT * FROM flags_old; DROP TABLE flags_old;"
            "CREATE INDEX IF NOT EXISTS idx_flags_patient ON flags(patient_id, date);")
    if cols and "goal_override" not in cols:
        con.execute("ALTER TABLE patients ADD COLUMN goal_override TEXT "
                    "CHECK (goal_override IS NULL OR goal_override = 'high_risk')")
    con.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "role TEXT NOT NULL CHECK (role IN ('chw', 'supervisor')), pin_salt TEXT NOT NULL, "
                "pin_hash TEXT NOT NULL, created_at TEXT)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS error_reviews (id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), "
        "kind TEXT CHECK (kind IN ('false_positive', 'false_negative', 'extraction_error')), "
        "source TEXT CHECK (source IN ('override_down', 'override_up', 'event_without_flag', 'referral_sent_home', "
        "'field_corrected', 'trajectory_miss')), linked_id INTEGER, rule_id TEXT, model_version TEXT, "
        "config_version TEXT, detected_on TEXT, evidence_json TEXT, supervisor_label TEXT NOT NULL DEFAULT 'pending' "
        "CHECK (supervisor_label IN ('confirmed_error', 'correct_call', 'unavoidable', 'pending')), cause_code TEXT "
        "CHECK (cause_code IS NULL OR cause_code IN ('threshold', 'trend_rule', 'symptom_rule', 'extraction', "
        "'silence', 'model', 'chw_judgement', 'other')), reviewed_by TEXT, reviewed_on TEXT, UNIQUE (source, kind, linked_id))")
    # Indexes for one laptop serving the whole clinic (also in data/schema.sql for new databases).
    for sql in ("CREATE INDEX IF NOT EXISTS idx_decisions_patient ON decisions(patient_id, date)",
                "CREATE INDEX IF NOT EXISTS idx_traj_patient ON trajectory_scores(patient_id, date)",
                "CREATE INDEX IF NOT EXISTS idx_plans_week ON plans(chw_id, week_start)",
                "CREATE INDEX IF NOT EXISTS idx_events_patient ON events(patient_id, date)",
                "CREATE INDEX IF NOT EXISTS idx_time_log_chw ON time_log(chw_id, activity)"):
        con.execute(sql)
    if "chw_id" in cols:
        con.execute("CREATE INDEX IF NOT EXISTS idx_patients_chw ON patients(chw_id, status)")
    con.commit()


@contextmanager
def _con():
    con = connect(DB)
    if DB not in _schema_checked:
        ensure_schema(con)
        _schema_checked.add(DB)
    try:
        yield con
    finally:
        con.close()


# ---------- PINs and roles ----------

def _hash_pin(pin, salt):
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(salt), 200_000).hex()


def add_user(name, role, pin):
    """Create a user with a PIN (run from the command line by whoever sets up the laptop)."""
    import secrets
    if role not in ("chw", "supervisor"):
        return {"error": "role must be chw or supervisor"}
    if not str(pin).isdigit() or len(str(pin)) < 4:
        return {"error": "the PIN must be at least 4 digits"}
    salt = secrets.token_hex(16)
    with _con() as con:
        for r in con.execute("SELECT pin_salt, pin_hash FROM users").fetchall():
            if _hash_pin(pin, r["pin_salt"]) == r["pin_hash"]:
                return {"error": "that PIN is already in use; choose another"}
        cur = con.execute("INSERT INTO users (name, role, pin_salt, pin_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                          (name, role, salt, _hash_pin(pin, salt), now_iso()))
        audit(con, "add_user", "users", cur.lastrowid, user_id="setup")
        con.commit()
        return {"id": cur.lastrowid, "name": name, "role": role}


def user_from_pin(con, pin):
    """The user whose PIN this is, or None."""
    if pin is None or str(pin) == "":
        return None
    for r in con.execute("SELECT * FROM users").fetchall():
        if _hash_pin(pin, r["pin_salt"]) == r["pin_hash"]:
            return dict(r)
    return None


def _patient_exists(con, patient_id):
    return con.execute("SELECT 1 FROM patients WHERE id = ?", (patient_id,)).fetchone() is not None


# ---------- status ----------

def status():
    """Config version, approved flag, model versions, LLM in use, and whether Ollama is reachable."""
    c = cfg()
    with _con() as con:
        ls = llm.status(c)
        return {
            "name": "TWESE CHW AI",
            "config_version": c["config_version"],
            "approved": c["approved"],
            "approved_by": c.get("approved_by", ""),
            "banner": None if c["approved"] else "Thresholds not yet physician-approved. Demonstration only.",
            "synthetic_data": get_meta(con, "synthetic") == "true",
            "models": {
                "decider": dec.model_version(con, c),
                "decider_reviewed_version": get_meta(con, "decider_version"),
                "julia_1": models.julia_status(c),
                "trajectory": traj.MODEL_VERSION,
                "llm": ls["model"],
                "llm_primary": c["llm"]["model"],
                "llm_fallback": c["llm"]["fallback_model"],
            },
            "ollama_reachable": ls["ollama_reachable"],
            "free_ram_gb": ls["free_ram_gb"],
            "who_risk_table_loaded": rk.load_base_table(c) is not None,
            "languages": content.languages(),
            "today": date.today().isoformat(),
        }


# ---------- A. dashboard ----------

def _latest_risk(con, patient_id):
    r = con.execute("SELECT * FROM risk_scores WHERE patient_id = ? ORDER BY date DESC, id DESC LIMIT 1",
                    (patient_id,)).fetchone()
    if not r:
        return None
    return {"date": r["date"], "score": r["score"], "tier": r["tier"], "components": loads(r["components_json"], {}),
            "config_version": r["config_version"]}


def _patient_row(con, p):
    pid = p["id"]
    series = bp_series(con, pid)
    last = series[-1] if series else None
    sugar = con.execute("SELECT blood_sugar, blood_sugar_unit, date FROM encounters WHERE patient_id = ? AND "
                        "blood_sugar IS NOT NULL ORDER BY date DESC LIMIT 1", (pid,)).fetchone()
    today = date.today().isoformat()
    nxt = con.execute("SELECT due_date FROM followups WHERE patient_id = ? AND kind = 'clinic_appointment' "
                      "AND done_on IS NULL AND due_date >= ? ORDER BY due_date LIMIT 1", (pid, today)).fetchone()
    missed = con.execute("SELECT due_date FROM followups WHERE patient_id = ? AND kind = 'clinic_appointment' "
                         "AND done_on IS NULL AND due_date < ? ORDER BY due_date", (pid, today)).fetchall()
    flags = signals.open_flags(con, pid)
    risk = _latest_risk(con, pid)
    contact = last_contact_date(con, pid)
    return {
        "id": pid, "age": p["age"], "sex": p["sex"], "village": p["village"], "language": p["language"],
        "condition_codes": loads(p["condition_codes"], []),
        "latest_bp": {"sbp": last["sbp"], "dbp": last["dbp"], "date": last["date"][:10]} if last else None,
        "last_six_bp": [{"date": r["date"][:10], "sbp": r["sbp"], "dbp": r["dbp"]} for r in series[-6:]],
        "latest_blood_sugar": ({"value": sugar["blood_sugar"], "unit": sugar["blood_sugar_unit"],
                                "date": sugar["date"][:10], "used": False} if sugar else None),
        "next_appointment": nxt["due_date"] if nxt else None,
        "missed_appointments": [m["due_date"] for m in missed],
        "tier": risk["tier"] if risk else None,
        "score": risk["score"] if risk else None,
        "open_flag_count": len(flags),
        "emergency": any(f["emergency"] == "yes" for f in flags),
        "last_contact": contact,
    }


def list_patients(filter_text=None, village=None, tier=None, chw=None):
    """One row per patient for the dashboard, highest risk first. chw: only that CHW's patients."""
    with _con() as con:
        sql, params = "SELECT * FROM patients WHERE status = 'active'", []
        if chw is not None:
            sql += " AND chw_id = ?"
            params.append(chw)
        rows = [_patient_row(con, p) for p in con.execute(sql + " ORDER BY id", params).fetchall()]
    if village:
        rows = [r for r in rows if (r["village"] or "").lower() == village.lower()]
    if tier:
        rows = [r for r in rows if r["tier"] == tier]
    if filter_text:
        f = filter_text.lower().strip()
        rows = [r for r in rows if f == str(r["id"]) or f in (r["village"] or "").lower()
                or any(f in c for c in r["condition_codes"])]
    rows.sort(key=lambda r: (not r["emergency"], -(r["score"] or 0), r["id"]))
    return {"count": len(rows), "patients": rows, "villages": sorted({r["village"] for r in rows if r["village"]})}


def emergency_protocol(lang="en"):
    """The protocol text for one language from config.emergency_protocol.file (English when the language
    is not in the file)."""
    c = cfg()
    p = c["emergency_protocol"]
    with open(root_path(p["file"]), encoding="utf-8") as f:
        text = f.read()
    names = {"english": "en", "français": "fr", "francais": "fr", "kiswahili": "sw", "swahili": "sw", "ikirundi": "rn"}
    sections, current = {}, None
    status = ""
    for line in text.split("\n"):
        if line.startswith("## "):
            current = names.get(line[3:].strip().lower(), line[3:].strip().lower())
            sections[current] = []
        elif current:
            sections[current].append(line)
        elif line.startswith("Status:"):
            status = line
    used = lang if lang in sections and lang in p.get("languages", [lang]) else "en"
    return {"lang": used, "requested_lang": lang, "text": "\n".join(sections.get(used, [])).strip(),
            "status": status, "file": p["file"], "source": p["source"],
            "sources_text": "\n".join(sections.get("sources", [])).strip()}


def escalation_list(lang="en"):
    """Patients with an emergency flag or an emergency_now suggestion, with the protocol text in one language."""
    c = cfg()
    with _con() as con:
        ids = pw.escalation_ids(con)
        out = []
        for pid in sorted(ids):
            p = con.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
            flags = [f for f in signals.open_flags(con, pid) if f["emergency"] == "yes"]
            out.append({"patient_id": pid, "age": p["age"], "village": p["village"],
                        "reasons": [f["reason_text"] for f in flags] or ["Suggested action: emergency_now"],
                        "flags": flags})
    return {"protocol": emergency_protocol(lang), "approved": c["approved"], "patients": out}


def patient_detail(patient_id, include_summary=True):
    """Everything about one patient: encounters, BP series, flags, suggestion, medications, understanding,
    follow-ups, risk, peer trajectory, and the French summary."""
    c = cfg()
    with _con() as con:
        p = con.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if p is None:
            return {"error": f"patient {patient_id} not found"}
        sig = signals.compute(con, c, patient_id)
        encs = []
        for e in con.execute("SELECT * FROM encounters WHERE patient_id = ? ORDER BY date DESC, id DESC",
                             (patient_id,)).fetchall():
            d = dict(e)
            for k in ("tests_performed", "positive_results", "referral", "symptom_codes"):
                d[k] = loads(d[k], [])
            encs.append(d)
        d = con.execute("SELECT * FROM decisions WHERE patient_id = ? ORDER BY date DESC, id DESC LIMIT 1",
                        (patient_id,)).fetchone()
        suggestion = None
        if d:
            st = loads(d["state_json"], {}) or {}
            suggestion = {"decision_id": d["id"], "date": d["date"], "suggestion": d["suggestion"],
                          "probs": loads(d["probs_json"], {}), "reason_text": st.get("reason_text"),
                          "guideline_ref": st.get("guideline_ref"), "state_text": st.get("text"),
                          "final_choice": d["final_choice"], "override_reason": d["override_reason"],
                          "model_version": d["model_version"], "config_version": d["config_version"]}
        meds = [dict(m) for m in con.execute("SELECT * FROM medications WHERE patient_id = ? ORDER BY id DESC",
                                             (patient_id,)).fetchall()]
        # The current list: the medications from the most recent encounter that has any.
        current = [m for m in meds if m["encounter_id"] == meds[0]["encounter_id"]] if meds else []
        t = con.execute("SELECT * FROM trajectory_scores WHERE patient_id = ? ORDER BY date DESC, id DESC LIMIT 1",
                        (patient_id,)).fetchone()
        out = {
            "patient": {**dict(p), "condition_codes": loads(p["condition_codes"], [])},
            "approved": c["approved"],
            "bp_series": [{"date": r["date"][:10], "sbp": r["sbp"], "dbp": r["dbp"], "source": r["source"]}
                          for r in bp_series(con, patient_id)],
            "baseline": {"sbp": sig["baseline_sbp"], "dbp": sig["baseline_dbp"], "readings": c["baseline_readings"]},
            "change_sbp": sig["change_sbp"], "slope_sbp": sig["slope_sbp"],
            "flags": signals.open_flags(con, patient_id),
            "suggestion": suggestion,
            "actions": c["actions"],
            "medications": current, "medication_history": meds,
            "understanding": dec._understanding(con, c, patient_id, iso(None)),
            "quiz_history": [dict(r) for r in con.execute(
                "SELECT * FROM quiz_results WHERE patient_id = ? ORDER BY asked_at DESC", (patient_id,)).fetchall()],
            "followups": fu.list_followups(con, patient_id),
            "risk": _latest_risk(con, patient_id),
            "trajectory": dict(t) if t else None,
            "encounters": encs,
            "last_contact": last_contact_date(con, patient_id),
        }
        if include_summary:
            out["summary_fr"] = llm.summary(con, c, patient_id)
    return out


def summary(patient_id):
    """Only the three-line French summary."""
    with _con() as con:
        if not _patient_exists(con, patient_id):
            return {"error": f"patient {patient_id} not found"}
        return llm.summary(con, cfg(), patient_id)


# ---------- B and C. SMS and monitoring ----------

def _message(row):
    d = dict(row)
    d["parsed_json"] = loads(d["parsed_json"], None)
    return d


def inbox(status="all"):
    """Incoming messages with their extracted fields and valid flag.
    status: new (not processed), needs_review (processed, not confirmed), confirmed, all."""
    with _con() as con:
        rows = [_message(r) for r in con.execute(
            "SELECT * FROM messages WHERE direction = 'in' ORDER BY received_at DESC, id DESC").fetchall()]
    for r in rows:
        pj = r["parsed_json"] or {}
        r["state"] = "confirmed" if pj.get("confirmed") else ("needs_review" if pj else "new")
    if status and status != "all":
        rows = [r for r in rows if r["state"] == status]
    return {"count": len(rows), "messages": rows[:200]}


def simulate_incoming(patient_id, text, lang=None, auto_process=True):
    """Insert an incoming SMS (the demo has no real gateway). A short reply while a quiz question is
    pending is recorded as the answer; anything else goes to the inbox and is processed."""
    c = cfg()
    with _con() as con:
        p = con.execute("SELECT language FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if p is None:
            return {"error": f"patient {patient_id} not found"}
        lang = lang or p["language"]
        cur = con.execute(
            "INSERT INTO messages (patient_id, direction, kind, lang, text, received_at) VALUES (?, 'in', 'other', ?, ?, ?)",
            (patient_id, lang, text, now_iso()))
        mid = cur.lastrowid
        con.commit()
        pending = qz.pending(con, patient_id)
        quiz_result = None
        if pending:
            # A bare 1, 2 or 3 is a quiz answer and nothing else. Free text that matches an option is
            # recorded too, but it still goes to the inbox: "face drooping" may be a real symptom report.
            q = content.quiz_question(pending["question_id"])
            option, _ = qz._match_option(c, q, pending["lang"] or lang, text)
            if option is not None:
                quiz_result = qz.record_answer(con, c, patient_id, pending["question_id"], text)
                rk.score_one(con, c, patient_id)
                if re.fullmatch(r"\s*[123]\s*[.)]?\s*", text):
                    con.execute("UPDATE messages SET kind = 'quiz', parsed_json = ? WHERE id = ?",
                                (dumps({"quiz_answer": quiz_result, "confirmed": True}), mid))
                    con.commit()
                    return {"message_id": mid, "routed_to": "quiz", "quiz": quiz_result}
            con.commit()
    out = {"message_id": mid, "routed_to": "inbox+quiz" if quiz_result else "inbox"}
    if quiz_result:
        out["quiz"] = quiz_result
    if auto_process:
        out["parsed"] = process_message(mid)
    return out


def process_message(message_id):
    """Run sms.py on one message: regex, intent, LLM extraction and checks. Returns the fields."""
    from engine import sms
    with _con() as con:
        return sms.process(con, cfg(), message_id)


def process_pending(limit=100):
    """Process waiting incoming SMS in one batch (oldest first). On one clinic laptop the local LLM takes about
    1.7 s per message, so messages from a gateway queue up and are processed here, not one request at a time."""
    from engine import sms
    c = cfg()
    with _con() as con:
        ids = [r[0] for r in con.execute(
            "SELECT id FROM messages WHERE direction = 'in' AND parsed_json IS NULL ORDER BY received_at, id LIMIT ?",
            (limit,)).fetchall()]
        results = [sms.process(con, c, i) for i in ids]
        left = con.execute("SELECT COUNT(*) FROM messages WHERE direction = 'in' AND parsed_json IS NULL").fetchone()[0]
    return {"processed": len(results), "still_waiting": left,
            "needs_manual": sum(1 for r in results if r.get("needs_manual")),
            "possible_emergency": [r["message_id"] for r in results if r.get("possible_emergency")]}


def confirm_fields(message_id, fields):
    """The CHW confirmed the fields: write the encounter, then refresh flags, suggestion, risk,
    and ask the next education question after a BP report."""
    from engine import sms
    c = cfg()
    with _con() as con:
        res = sms.confirm(con, c, message_id, fields)
        if "error" in res:
            return res
        pid = res["patient_id"]
        out = {**res}
        if res["encounter_id"]:
            # The outcome of the previous confirmed decision, for the training data.
            e = con.execute("SELECT * FROM encounters WHERE id = ?", (res["encounter_id"],)).fetchone()
            sig = signals.refresh(con, c, pid)
            outcome = (f"next contact {e['date'][:10]}: BP {e['bp_text'] or '-'}, flags: "
                       f"{', '.join(f['type'] for f in sig['flags']) or 'none'}")
            con.execute("UPDATE decisions SET outcome_at_next_contact = ? WHERE patient_id = ? AND final_choice IS NOT "
                        "NULL AND outcome_at_next_contact IS NULL", (outcome, pid))
            fu.mark_done(con, pid, "sms_check", e["date"])
            out["borderline_watch"] = fu.borderline_watch(con, c, pid, sig["flags"])
            traj.score_one(con, c, pid)  # only this patient's window changed; the shared index is kept
            out["flags"] = sig["flags"]
            out["suggestion"] = {k: v for k, v in dec.decide(con, c, pid).items() if k != "state"}
            out["risk"] = rk.score_one(con, c, pid)
            out["escalation"] = pid in pw.escalation_ids(con)
            if res.get("intent") == "bp_report" and "sms_bp_report" in c["quiz"]["ask_after"] and res["fields"].get("sbp"):
                out["quiz_question"] = qz.next_question(con, c, pid, "sms")
        con.commit()
    return out


def decide(patient_id):
    """Suggest the next action for one patient and store it."""
    with _con() as con:
        if not _patient_exists(con, patient_id):
            return {"error": f"patient {patient_id} not found"}
        c = cfg()
        signals.refresh(con, c, patient_id)
        out = dec.decide(con, c, patient_id)
    out.pop("state", None)
    return out


def confirm_decision(patient_id, final_choice, override_reason=""):
    """The CHW confirms or overrides the suggestion. The choice is stored, and the follow-up is scheduled."""
    with _con() as con:
        return dec.confirm(con, cfg(), patient_id, final_choice, override_reason)


def risk_all(date=None):
    """Score every patient; returns the scores, highest first."""
    with _con() as con:
        c = cfg()
        for (pid,) in con.execute("SELECT id FROM patients WHERE status = 'active'").fetchall():
            sig = signals.refresh(con, c, pid, date)
            fu.borderline_watch(con, c, pid, sig["flags"], date)
        traj.score_all(con, c, date)  # off (flag no) until config.trajectory.min_events local events exist
        scores = rk.risk_all(con, c, date)
    return {"date": iso(date), "count": len(scores),
            "tiers": {t: sum(s["tier"] == t for s in scores) for t in ("high", "medium", "low")},
            "who_table_loaded": rk.load_base_table(c) is not None, "scores": scores}


def trajectory(patient_id, min_events=None):
    """The peer-trajectory result: share of similar local patterns followed by an event, the reason,
    and anonymised peer BP series."""
    with _con() as con:
        if not _patient_exists(con, patient_id):
            return {"error": f"patient {patient_id} not found"}
        return traj.for_patient(con, cfg(), patient_id, min_events=min_events)


def set_goal_override(patient_id, goal_override, pin):
    """A supervisor sets or clears a patient's goal_override ("high_risk" or None). The role is checked from
    the PIN before anything is written. Flags and the risk score are refreshed with the new goal."""
    if goal_override not in (None, "high_risk"):
        return {"error": "goal_override must be high_risk or null"}
    c = cfg()
    with _con() as con:
        user = user_from_pin(con, pin)
        if user is None:
            return {"error": "PIN not recognised"}
        if user["role"] != "supervisor":
            return {"error": "only a supervisor can change a patient's BP goal"}
        if not _patient_exists(con, patient_id):
            return {"error": f"patient {patient_id} not found"}
        con.execute("UPDATE patients SET goal_override = ? WHERE id = ?", (goal_override, patient_id))
        audit(con, f"set_goal_override:{goal_override}", "patients", patient_id, user_id=user["name"])
        con.commit()
        sig = signals.refresh(con, c, patient_id)
        rk.score_one(con, c, patient_id)
        return {"patient_id": patient_id, "goal_override": goal_override, "goal": sig["goal"],
                "set_by": user["name"], "flags": sig["flags"]}


def import_records(csv_text):
    """Import existing records from CSV text (the columns in data/import_records.py)."""
    import tempfile
    from data.import_records import import_csv
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(csv_text)
        path = f.name
    try:
        with _con() as con:
            return import_csv(con, path)
    finally:
        os.remove(path)


# ---------- C. outgoing SMS ----------

def outgoing_queue():
    """Outgoing SMS waiting to be sent (reminders, follow-ups, quiz questions, corrections)."""
    with _con() as con:
        fu.queue_reminders(con, cfg())
        rows = fu.outgoing_queue(con)
    return {"count": len(rows), "messages": rows}


def mark_sent(message_id):
    with _con() as con:
        return fu.mark_sent(con, message_id)


def sync():
    """Mark every queued outgoing SMS as sent ("the signal comes back")."""
    with _con() as con:
        ids = [r["id"] for r in fu.outgoing_queue(con)]
        for i in ids:
            fu.mark_sent(con, i)
    return {"sent": ids}


def followups(patient_id=None):
    with _con() as con:
        return fu.list_followups(con, patient_id)


def phone_thread(patient_id):
    """Every incoming and outgoing SMS for one patient, oldest first, for the virtual phone."""
    with _con() as con:
        p = con.execute("SELECT id, language FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if p is None:
            return {"error": f"patient {patient_id} not found"}
        rows = con.execute("SELECT id, direction, kind, lang, text, received_at, status, parsed_json FROM messages "
                           "WHERE patient_id = ? ORDER BY received_at, id", (patient_id,)).fetchall()
    msgs = []
    for r in rows:
        pj = loads(r["parsed_json"], {}) or {}
        msgs.append({"id": r["id"], "direction": r["direction"], "kind": r["kind"], "lang": r["lang"],
                     "text": r["text"], "at": r["received_at"], "status": r["status"],
                     "delivered": r["direction"] == "in" or r["status"] == "sent",
                     "state": ("confirmed" if pj.get("confirmed") else "needs_review" if pj else "new")
                     if r["direction"] == "in" else r["status"]})
    return {"patient_id": patient_id, "language": p["language"], "messages": msgs}


# ---------- D. weekly plan and hours ----------

def plan_week(chw_id=1, week_start=None, hours=None, regenerate=False):
    """The weekly plan (made on first call, kept until the hours change)."""
    c = cfg()
    week_start = week_start or (date.today() + timedelta(days=(7 - date.today().weekday()) % 7)).isoformat()
    with _con() as con:
        # Every patient needs today's score before planning (one confirmed SMS scores only one patient).
        scored = con.execute("SELECT COUNT(DISTINCT patient_id) FROM risk_scores WHERE date = ?", (iso(None),)).fetchone()[0]
        if scored < con.execute("SELECT COUNT(*) FROM patients WHERE status = 'active'").fetchone()[0]:
            rk.risk_all(con, c)
        return pw.plan_week(con, c, chw_id, week_start, hours, regenerate)


def update_plan(plan_id, action):
    with _con() as con:
        return pw.update_plan(con, cfg(), plan_id, action)


def approve_plan(chw_id, week_start):
    with _con() as con:
        return pw.approve_plan(con, cfg(), chw_id, week_start)


def hours(chw_id=1, week_start=None):
    week_start = week_start or (date.today() - timedelta(days=date.today().weekday())).isoformat()
    with _con() as con:
        return hrs.hours(con, cfg(), chw_id, week_start)


def log_time(chw_id, activity, patient_id=None, start=None, end=None):
    """Log CHW time. A visit also closes the patient's open CHW follow-up, marks this week's planned
    visit done, and returns the next education question to ask in person."""
    c = cfg()
    with _con() as con:
        out = hrs.log_time(con, chw_id, activity, patient_id, start, end)
        if "error" in out or activity != "visit" or not patient_id:
            return out
        day = start[:10]
        fu.mark_done(con, patient_id, "chw_visit", day)
        week = (date.fromisoformat(day) - timedelta(days=date.fromisoformat(day).weekday())).isoformat()
        con.execute("UPDATE plans SET done_on = ? WHERE patient_id = ? AND chw_id = ? AND action = 'visit' "
                    "AND week_start BETWEEN ? AND ? AND done_on IS NULL",
                    (day, patient_id, chw_id, (date.fromisoformat(week) - timedelta(days=6)).isoformat(), week))
        con.commit()
        if "chw_visit" in c["quiz"]["ask_after"]:
            out["quiz_question"] = qz.next_question(con, c, patient_id, "visit")
    return out


# ---------- E. education ----------

def next_question(patient_id, channel="sms"):
    with _con() as con:
        if not _patient_exists(con, patient_id):
            return {"error": f"patient {patient_id} not found"}
        return qz.next_question(con, cfg(), patient_id, channel)


def record_answer(patient_id, question_id, reply_text):
    c = cfg()
    with _con() as con:
        out = qz.record_answer(con, c, patient_id, question_id, reply_text)
        if "error" not in out:
            rk.score_one(con, c, patient_id)
        return out


def support_needed():
    with _con() as con:
        return qz.support_needed(con, cfg())


# ---------- metrics, review, feedback, demo ----------

def metrics(month=None):
    from engine import metrics as m
    month = month or date.today().strftime("%Y-%m")
    with _con() as con:
        return m.metrics(con, cfg(), month)


def run_review():
    """The monthly review, pressed by a person: retrain, store the new model version, return the scores."""
    from engine import errors, retrain
    with _con() as con:
        found = errors.detect(con, cfg())
        out = retrain.run_review(con, cfg())
        out["error_reviews"] = {"new": found, **errors.summary(con, cfg(), date.today().strftime("%Y-%m"))}
        audit(con, "run_review", "meta", None, user_id="supervisor")
        con.commit()
    return out


# ---------- error reviews (PLAN.md step 11c) ----------

def detect_errors(as_of=None):
    """Look for new suspected misses and over-calls; returns the new rows per source and the totals."""
    from engine import errors
    c = cfg()
    with _con() as con:
        found = errors.detect(con, c, as_of)
        return {"new": found, **errors.summary(con, c)}


def error_reviews(label=None):
    """The error reviews, newest first (label: pending, confirmed_error, correct_call, unavoidable)."""
    from engine import errors
    with _con() as con:
        return {"reviews": errors.list_reviews(con, label)}


def error_summary(month=None):
    """Per rule and per model version: flags raised, confirmed false positives and negatives, pending reviews,
    low-yield visits, and the sensitivity and precision proxies (month = YYYY-MM, or all time)."""
    from engine import errors
    with _con() as con:
        return errors.summary(con, cfg(), month)


def replay_config(changes):
    """Re-run the rules on the stored encounters with a changed config, before anyone makes the change:
    how many flags would fire, and which recorded events would gain or lose a prior flag. Writes nothing."""
    from engine import errors
    with _con() as con:
        return errors.replay(con, cfg(), changes)


def label_error_review(review_id, supervisor_label, cause_code, pin):
    """A supervisor labels one error review. The role is checked from the PIN before anything is written."""
    from engine import errors
    with _con() as con:
        user = user_from_pin(con, pin)
        if user is None:
            return {"error": "PIN not recognised"}
        if user["role"] != "supervisor":
            return {"error": "only a supervisor can label error reviews"}
        return errors.label(con, review_id, supervisor_label, cause_code, user["name"])


def record_referral_outcome(patient_id, outcome):
    """After a referral or emergency (protocol step 7): seen, admitted, treatment_changed, sent_home (no change)
    or not_reached. Stored on the latest refer_clinic / emergency_now decision; admitted also records the hard
    outcome in events. sent_home becomes a candidate false positive; admitted and treatment_changed confirm the call."""
    if outcome not in ("seen", "admitted", "treatment_changed", "sent_home", "not_reached"):
        return {"error": "outcome must be seen, admitted, treatment_changed, sent_home or not_reached"}
    with _con() as con:
        d = con.execute("SELECT * FROM decisions WHERE patient_id = ? AND final_choice IN ('refer_clinic', 'emergency_now') "
                        "ORDER BY date DESC, id DESC LIMIT 1", (patient_id,)).fetchone()
        if d is None:
            return {"error": "no confirmed referral or emergency for this patient"}
        con.execute("UPDATE decisions SET outcome_at_next_contact = ? WHERE id = ?",
                    (f"referral: {outcome} ({now_iso()[:10]})", d["id"]))
        event_id = None
        if outcome == "admitted":
            event_id = con.execute(
                "INSERT INTO events (patient_id, date, type, source, note) VALUES (?, ?, ?, 'referral', ?)",
                (patient_id, now_iso()[:10], "emergency_referral_confirmed" if d["final_choice"] == "emergency_now"
                 else "hospital_admission", f"Admitted after decision {d['id']}")).lastrowid
        audit(con, f"referral_outcome:{outcome}", "decisions", d["id"])
        con.commit()
        return {"decision_id": d["id"], "final_choice": d["final_choice"], "outcome": outcome, "event_id": event_id}


def feedback(who, target, value, note=""):
    if who not in ("chw", "patient") or target not in ("suggestion", "plan"):
        return {"error": "who must be chw or patient; target must be suggestion or plan"}
    with _con() as con:
        cur = con.execute("INSERT INTO feedback (who, target, value, note) VALUES (?, ?, ?, ?)", (who, target, value, note))
        con.commit()
        return {"id": cur.lastrowid, "who": who, "target": target, "value": value}


def demo_script():
    """The ordered demo steps from docs/demo_script.json."""
    with open(os.path.join(ROOT, "docs", "demo_script.json"), encoding="utf-8") as f:
        return json.load(f)


def reset_demo(seed=1):
    """Rebuild the synthetic database (same seed, so the same patients), so the demo can run again."""
    subprocess.run([sys.executable, os.path.join(ROOT, "data", "make_synthetic.py"), "--db", root_path(DB),
                    "--config", root_path(CONFIG), "--seed", str(seed)], check=True, capture_output=True)
    with _con() as con:
        from engine import context
        context.backfill(con, cfg(), use_llm=False)  # context codes for the table decider, from the phrase list
        traj.score_all(con, cfg())
        rk.risk_all(con, cfg())
        for (pid,) in con.execute("SELECT id FROM patients WHERE status = 'active'").fetchall():
            fu.borderline_watch(con, cfg(), pid, signals.open_flags(con, pid))
    return {"reset": True, "seed": seed, "status": status()}


def languages():
    return {"languages": content.languages()}


def main():
    """Command line for laptop setup: python engine/api.py add-user --name "Nurse A" --role supervisor --pin 4821"""
    import argparse
    parser = argparse.ArgumentParser(description="TWESE engine setup commands.")
    parser.add_argument("command", choices=["add-user"])
    parser.add_argument("--db", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", required=True, choices=["chw", "supervisor"])
    parser.add_argument("--pin", required=True)
    args = parser.parse_args()
    configure(args.db, args.config)
    print(add_user(args.name, args.role, args.pin))


if __name__ == "__main__":
    main()
