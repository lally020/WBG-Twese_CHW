"""Step 11c. Catching false positives and false negatives (PLAN.md).

The tool watches its own mistakes. Neither kind can be seen at the moment of the decision; both become visible
later, from what the CHW did and what happened to the patient. detect() collects every signal into error_reviews,
one row each (never twice for the same linked row), and a supervisor labels them at the monthly review.
Nothing changes a threshold or a model by itself.

Signals:
- override_down / override_up      the CHW chose a lower / higher action than the suggestion
                                   -> candidate false_positive / false_negative
- event_without_flag               an event with no flag of any type in config.error_review.event_lookback_days
                                   before it -> false_negative, cause pre-filled: silence, rule, extraction, model
                                   or unknown
- referral_sent_home               a referral whose outcome is "sent home, no change" -> candidate false_positive
                                   (admitted or treatment changed confirm the call)
- field_corrected                  the CHW edited an extracted field -> extraction_error (per model version)
- trajectory_miss                  a trajectory flag with no event in the horizon -> candidate false_positive;
                                   an event in a patient the trajectory model scored low -> false_negative
Low-yield visits (a visit_this_week whose visit found BP at goal, medicine taken, no symptoms) are not errors;
summary() shows them as the cost of sensitivity next to the false negatives.

replay(changes) re-runs signals.py over the stored encounters with a changed config, so a threshold change is
seen before it is made: how many flags would fire, and which recorded events would gain or lose a prior flag.

Usage:
    python engine/errors.py --db data/chw.db --config config/guideline_htn.json [--as-of 2026-10-03]
"""

import argparse
import copy
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import action_rank, add_days, audit, connect, dumps, iso, load_config, loads, now_iso

LABELS = ("confirmed_error", "correct_call", "unavoidable", "pending")
CAUSES = ("threshold", "trend_rule", "symptom_rule", "extraction", "silence", "model", "chw_judgement", "other")


# ---------- small helpers ----------

def rule_id(flag):
    """One name per rule: threshold:<id>, trend, symptom:<code>, who_risk:<level>, peer_trajectory."""
    t, v, reason = flag.get("type"), flag.get("value"), flag.get("reason_text") or ""
    if t == "threshold":
        m = re.search(r"'([a-z0-9_]+)' cut-off", reason)
        return f"threshold:{m.group(1)}" if m else "threshold"
    if t == "symptom":
        return f"symptom:{v}"
    if t == "who_risk":
        m = re.search(r"'([a-z_]+)' level", reason)
        return f"who_risk:{m.group(1)}" if m else "who_risk"
    return t or "unknown"


def _add(con, patient_id, kind, source, linked_id, rule, model_version, config_version, evidence, cause=None):
    """Insert one row unless this (source, kind, linked_id) is already there. Returns 1 when added."""
    cur = con.execute(
        "INSERT OR IGNORE INTO error_reviews (patient_id, kind, source, linked_id, rule_id, model_version, "
        "config_version, detected_on, evidence_json, cause_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (patient_id, kind, source, linked_id, rule, model_version, config_version, now_iso(), dumps(evidence), cause))
    return cur.rowcount


def _symptom_words():
    """Codes and the words patients use for them (engine/llm.py), to spot a symptom missed by extraction."""
    from engine.llm import SYMPTOM_HINTS
    return {code: [code.replace("_", " ")] + [w.strip().lower() for w in hints.split(",")]
            for code, hints in SYMPTOM_HINTS.items()}


def prior_flags(con, cfg, patient_id, event_date, lookback):
    """Flags of any type at each contact in the lookback window before an event (computed with cfg)."""
    from engine import signals
    start = add_days(event_date, -lookback)
    contacts = [r["d"] for r in con.execute(
        "SELECT DISTINCT substr(date, 1, 10) AS d FROM encounters WHERE patient_id = ? AND date >= ? AND date < ? "
        "ORDER BY d", (patient_id, start, event_date[:10])).fetchall()]
    found = []
    for d in contacts:
        fl = signals.compute(con, cfg, patient_id, d)["flags"]
        if fl:
            found.append({"date": d, "rules": sorted({rule_id(f) for f in fl})})
    return contacts, found


def _cause_for_event(con, patient_id, start, end, contacts):
    """Pre-fill the cause of an event that had no prior flag: silence, model, extraction, rule or unknown."""
    readings = con.execute("SELECT COUNT(*) FROM encounters WHERE patient_id = ? AND sbp IS NOT NULL "
                           "AND date >= ? AND date < ?", (patient_id, start, end)).fetchone()[0]
    if not readings:
        return "silence", "no readings in the window"
    for d in con.execute("SELECT state_json, suggestion FROM decisions WHERE patient_id = ? AND date >= ? AND date < ?",
                         (patient_id, start, end)).fetchall():
        st = loads(d["state_json"], {}) or {}
        if st.get("rule_default") and d["suggestion"] in ("routine", "recheck_7_days", "visit_this_week") \
                and st["rule_default"] != d["suggestion"] and d["suggestion"] != "not_sure":
            return "model", f"the model stepped down from {st['rule_default']} to {d['suggestion']}"
    words = _symptom_words()
    texts = [(r["t"] or "", r["codes"]) for r in con.execute(
        "SELECT COALESCE(complaint_text, '') || ' ' || COALESCE(chw_notes_text, '') AS t, symptom_codes AS codes "
        "FROM encounters WHERE patient_id = ? AND date >= ? AND date < ?", (patient_id, start, end)).fetchall()]
    texts += [(r["text"], (loads(r["parsed_json"], {}) or {}).get("fields", {}).get("symptoms")) for r in con.execute(
        "SELECT text, parsed_json FROM messages WHERE patient_id = ? AND direction = 'in' AND received_at >= ? "
        "AND received_at < ?", (patient_id, start, end)).fetchall()]
    for text, codes in texts:
        coded = set(loads(codes, []) if isinstance(codes, str) else (codes or []))
        for code, ws in words.items():
            if code not in coded and any(w and w in text.lower() for w in ws):
                return "extraction", f"'{code}' is in the text \"{text.strip()[:120]}\" but not in symptom_codes"
    return "rule", "readings present but no rule fired"


# ---------- detection ----------

def _overrides(con, cfg):
    n = 0
    for d in con.execute("SELECT * FROM decisions WHERE final_choice IS NOT NULL AND final_choice != suggestion "
                         "AND suggestion != 'not_sure' AND final_choice != 'not_sure'").fetchall():
        st = loads(d["state_json"], {}) or {}
        up = action_rank(cfg, d["final_choice"]) > action_rank(cfg, d["suggestion"])
        rules = sorted({rule_id(f) for f in st.get("flags", [])})
        n += _add(con, d["patient_id"], "false_negative" if up else "false_positive",
                  "override_up" if up else "override_down", d["id"], "; ".join(rules) or "none",
                  d["model_version"], d["config_version"],
                  {"decision_id": d["id"], "date": d["date"], "suggestion": d["suggestion"],
                   "final_choice": d["final_choice"], "override_reason": d["override_reason"],
                   "rule_default": st.get("rule_default"), "state_text": (st.get("text") or "")[:600]})
    return n


def _events_without_flag(con, cfg, as_of):
    lookback = cfg.get("error_review", {}).get("event_lookback_days", 90)
    n = 0
    for e in con.execute("SELECT * FROM events WHERE date <= ?", (as_of + "T99",)).fetchall():
        contacts, found = prior_flags(con, cfg, e["patient_id"], e["date"][:10], lookback)
        if found:
            continue
        start = add_days(e["date"], -lookback)
        cause, why = _cause_for_event(con, e["patient_id"], start, e["date"][:10], contacts)
        n += _add(con, e["patient_id"], "false_negative", "event_without_flag", e["id"], None, None,
                  cfg["config_version"],
                  {"event_id": e["id"], "event": e["type"], "event_date": e["date"][:10], "lookback_days": lookback,
                   "contacts_in_window": contacts, "prefilled_cause": cause, "why": why},
                  cause=cause if cause in CAUSES else None)
    return n


def _referrals_sent_home(con, cfg):
    n = 0
    for d in con.execute("SELECT * FROM decisions WHERE final_choice IN ('refer_clinic', 'emergency_now') "
                         "AND outcome_at_next_contact LIKE 'referral: sent_home%'").fetchall():
        st = loads(d["state_json"], {}) or {}
        rules = sorted({rule_id(f) for f in st.get("flags", [])})
        n += _add(con, d["patient_id"], "false_positive", "referral_sent_home", d["id"], "; ".join(rules) or "none",
                  d["model_version"], d["config_version"],
                  {"decision_id": d["id"], "date": d["date"], "final_choice": d["final_choice"],
                   "outcome": d["outcome_at_next_contact"]})
    return n


def _fields_corrected(con, cfg):
    n = 0
    for m in con.execute("SELECT * FROM messages WHERE direction = 'in' AND parsed_json LIKE '%confirmed_fields%'").fetchall():
        pj = loads(m["parsed_json"], {}) or {}
        got, final = pj.get("fields") or {}, pj.get("confirmed_fields") or {}
        if not got:
            continue  # nothing was extracted (manual entry), so nothing was corrected
        changed = {}
        for k in ("sbp", "dbp", "meds_taken", "symptoms"):
            a, b = got.get(k), final.get(k)
            if k == "symptoms":
                a, b = sorted(a or []), sorted(b or [])
            if a != b:
                changed[k] = {"extracted": a, "confirmed": b}
        if changed:
            n += _add(con, m["patient_id"], "extraction_error", "field_corrected", m["id"], "extraction:" + ",".join(changed),
                      (pj.get("llm") or {}).get("model") or pj.get("backend"), cfg["config_version"],
                      {"message_id": m["id"], "text": m["text"], "changed": changed, "intent": pj.get("intent")},
                      cause="extraction")
    return n


def _trajectory(con, cfg, as_of):
    horizon = cfg["trajectory"]["horizon_days"]
    n = 0
    # An event in a patient the trajectory model scored low: false negative.
    for e in con.execute("SELECT * FROM events WHERE date <= ?", (as_of + "T99",)).fetchall():
        rows = con.execute("SELECT * FROM trajectory_scores WHERE patient_id = ? AND date < ? AND date >= ? "
                           "AND share_peers_with_event IS NOT NULL ORDER BY date DESC",
                           (e["patient_id"], e["date"][:10], add_days(e["date"], -horizon))).fetchall()
        if rows and all(r["flag"] == "no" for r in rows):
            last = rows[0]
            n += _add(con, e["patient_id"], "false_negative", "trajectory_miss", e["id"], "peer_trajectory",
                      last["model_version"], cfg["config_version"],
                      {"event_id": e["id"], "event": e["type"], "event_date": e["date"][:10],
                       "last_score_date": last["date"], "last_share": last["share_peers_with_event"]}, cause="model")
    # A trajectory flag with no event in the horizon (once the horizon has passed): candidate false positive.
    for t in con.execute("SELECT * FROM trajectory_scores WHERE flag = 'yes' AND date <= ?",
                         (add_days(as_of, -horizon),)).fetchall():
        hit = con.execute("SELECT 1 FROM events WHERE patient_id = ? AND date > ? AND date <= ?",
                          (t["patient_id"], t["date"], add_days(t["date"], horizon) + "T99")).fetchone()
        if not hit:
            n += _add(con, t["patient_id"], "false_positive", "trajectory_miss", t["id"], "peer_trajectory",
                      t["model_version"], cfg["config_version"],
                      {"trajectory_score_id": t["id"], "date": t["date"], "share": t["share_peers_with_event"],
                       "horizon_days": horizon})
    return n


def detect(con, cfg, as_of=None):
    """Look for new evidence of misses and over-calls. Returns the number of new rows per source."""
    as_of = iso(as_of)
    found = {"override": _overrides(con, cfg), "event_without_flag": _events_without_flag(con, cfg, as_of),
             "referral_sent_home": _referrals_sent_home(con, cfg), "field_corrected": _fields_corrected(con, cfg),
             "trajectory_miss": _trajectory(con, cfg, as_of)}
    con.commit()
    return found


def low_yield_visits(con, cfg, start=None, end=None):
    """visit_this_week decisions whose next CHW visit found BP at goal, medicine taken and no symptoms.
    Not errors: the hours they cost are the price of sensitivity."""
    from engine.signals import at_goal, patient_goal
    window = cfg.get("error_review", {}).get("visit_window_days", 14)
    sql, params = "SELECT * FROM decisions WHERE final_choice = 'visit_this_week'", []
    if start:
        sql += " AND date >= ? AND date < ?"
        params += [start, end]
    out = []
    for d in con.execute(sql, params).fetchall():
        v = con.execute("SELECT * FROM encounters WHERE patient_id = ? AND source = 'chw' AND date > ? AND date <= ? "
                        "ORDER BY date LIMIT 1", (d["patient_id"], d["date"], add_days(d["date"], window) + "T99")).fetchone()
        if v is None or v["sbp"] is None:
            continue
        goal = patient_goal(cfg, con.execute("SELECT * FROM patients WHERE id = ?", (d["patient_id"],)).fetchone())
        syms = [s for s in loads(v["symptom_codes"], []) if s and s != "none"]
        if at_goal(goal, v) and v["meds_taken"] == "yes" and not syms:
            st = loads(d["state_json"], {}) or {}
            out.append({"decision_id": d["id"], "patient_id": d["patient_id"], "visit_date": v["date"][:10],
                        "rules": sorted({rule_id(f) for f in st.get("flags", [])}) or ["none"],
                        "model_version": d["model_version"]})
    return out


# ---------- summary for the monthly review ----------

def _bounds(month):
    if not month:
        return None, None
    y, m = int(month[:4]), int(month[5:7])
    return f"{y:04d}-{m:02d}-01", f"{y + (m == 12):04d}-{m % 12 + 1:02d}-01"


def summary(con, cfg, month=None):
    """Per rule_id and per model_version: flags raised, confirmed false positives and false negatives, pending
    reviews, low-yield visits; and the sensitivity proxy (share of events with a prior flag) and the precision
    proxy (share of flags followed by a confirmed action). month = 'YYYY-MM', or None for all time."""
    start, end = _bounds(month)
    window = cfg.get("error_review", {}).get("action_window_days", 14)
    lookback = cfg.get("error_review", {}).get("event_lookback_days", 90)
    in_month = (lambda col: f" WHERE {col} >= ? AND {col} < ?") if start else (lambda col: "")
    p = (start, end) if start else ()

    reviews = [dict(r) for r in con.execute("SELECT * FROM error_reviews" + in_month("detected_on"), p).fetchall()]
    flags = [dict(r) for r in con.execute("SELECT * FROM flags" + in_month("date"), p).fetchall()]
    low = low_yield_visits(con, cfg, start, end)

    per_rule = {}

    def row(rid):
        return per_rule.setdefault(rid, {"flags_raised": 0, "confirmed_false_positive": 0, "confirmed_false_negative": 0,
                                         "pending": 0, "low_yield_visits": 0, "flags_followed_by_action": 0})

    for f in flags:
        r = row(rule_id(f))
        r["flags_raised"] += 1
        acted = con.execute("SELECT 1 FROM decisions WHERE patient_id = ? AND date >= ? AND date <= ? "
                            "AND final_choice IS NOT NULL AND final_choice NOT IN ('routine', 'not_sure')",
                            (f["patient_id"], f["date"], add_days(f["date"], window))).fetchone()
        r["flags_followed_by_action"] += bool(acted)
    for rv in reviews:
        for rid in (rv["rule_id"] or "none").split("; "):
            r = row(rid)
            if rv["supervisor_label"] == "pending":
                r["pending"] += 1
            elif rv["supervisor_label"] == "confirmed_error" and rv["kind"] == "false_positive":
                r["confirmed_false_positive"] += 1
            elif rv["supervisor_label"] == "confirmed_error" and rv["kind"] == "false_negative":
                r["confirmed_false_negative"] += 1
    for v in low:
        for rid in v["rules"]:
            row(rid)["low_yield_visits"] += 1
    for r in per_rule.values():
        r["precision_proxy"] = round(r["flags_followed_by_action"] / r["flags_raised"], 2) if r["flags_raised"] else None

    per_model = {}
    for rv in reviews:
        m = per_model.setdefault(rv["model_version"] or "none", {"reviews": 0, "confirmed_false_positive": 0,
                                                               "confirmed_false_negative": 0, "extraction_errors": 0,
                                                               "pending": 0})
        m["reviews"] += 1
        m["pending"] += rv["supervisor_label"] == "pending"
        m["extraction_errors"] += rv["kind"] == "extraction_error"
        if rv["supervisor_label"] == "confirmed_error":
            m["confirmed_false_positive"] += rv["kind"] == "false_positive"
            m["confirmed_false_negative"] += rv["kind"] == "false_negative"

    events = con.execute("SELECT * FROM events" + in_month("date"), p).fetchall()
    with_flag = sum(bool(prior_flags(con, cfg, e["patient_id"], e["date"][:10], lookback)[1]) for e in events)
    count = lambda key: {k: sum(r[key] == k for r in reviews) for k in sorted({r[key] for r in reviews if r[key]})}  # noqa: E731
    return {
        "month": month, "total": len(reviews), "by_kind": count("kind"), "by_source": count("source"),
        "by_label": count("supervisor_label"),
        "confirmed_by_cause": {c: sum(r["cause_code"] == c and r["supervisor_label"] == "confirmed_error" for r in reviews)
                               for c in CAUSES if any(r["cause_code"] == c for r in reviews)},
        "per_rule": dict(sorted(per_rule.items())), "per_model_version": per_model,
        "low_yield_visits": len(low),
        "sensitivity_proxy": {"events": len(events), "with_prior_flag": with_flag,
                              "share": round(with_flag / len(events), 2) if events else None},
        "precision_proxy": {"flags": len(flags), "followed_by_action": sum(r["flags_followed_by_action"] for r in per_rule.values()),
                            "share": round(sum(r["flags_followed_by_action"] for r in per_rule.values()) / len(flags), 2)
                            if flags else None},
    }


def list_reviews(con, label=None, limit=200):
    sql, params = "SELECT * FROM error_reviews", []
    if label:
        sql += " WHERE supervisor_label = ?"
        params.append(label)
    rows = [dict(r) for r in con.execute(sql + " ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()]
    for r in rows:
        r["evidence"] = loads(r.pop("evidence_json"), {})
    return rows


def label(con, review_id, supervisor_label, cause_code, reviewed_by):
    """Store a supervisor's label (engine/api.py checks the PIN and role first)."""
    if supervisor_label not in LABELS:
        return {"error": f"supervisor_label must be one of {LABELS}"}
    if cause_code is not None and cause_code not in CAUSES:
        return {"error": f"cause_code must be one of {CAUSES}"}
    if supervisor_label == "confirmed_error" and cause_code is None:
        return {"error": "a confirmed error needs a cause_code"}
    n = con.execute("UPDATE error_reviews SET supervisor_label = ?, cause_code = ?, reviewed_by = ?, reviewed_on = ? "
                    "WHERE id = ?", (supervisor_label, cause_code, reviewed_by, now_iso(), review_id)).rowcount
    if not n:
        return {"error": f"error review {review_id} not found"}
    audit(con, f"label_error_review:{supervisor_label}", "error_reviews", review_id, user_id=reviewed_by)
    con.commit()
    return dict(con.execute("SELECT * FROM error_reviews WHERE id = ?", (review_id,)).fetchone())


# ---------- replay ----------

def _merge(base, changes):
    out = copy.deepcopy(base)
    for k, v in (changes or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def replay(con, cfg, changes):
    """Re-run the rules over every stored contact with a changed config (changes is merged into the current
    config). Reports flags fired now and with the change, and which recorded events gain or lose a prior flag.
    Writes nothing."""
    from engine import signals
    t0 = time.time()
    candidate = _merge(cfg, changes)
    lookback = cfg.get("error_review", {}).get("event_lookback_days", 90)
    contacts = con.execute("SELECT DISTINCT patient_id, substr(date, 1, 10) AS d FROM encounters "
                           "WHERE sbp IS NOT NULL ORDER BY patient_id, d").fetchall()
    stats = {"now": {"contacts_flagged": 0, "flags": 0, "by_rule": {}},
             "candidate": {"contacts_flagged": 0, "flags": 0, "by_rule": {}}}
    flagged = {"now": set(), "candidate": set()}
    for c in contacts:
        for name, conf in (("now", cfg), ("candidate", candidate)):
            fl = signals.compute(con, conf, c["patient_id"], c["d"])["flags"]
            if fl:
                stats[name]["contacts_flagged"] += 1
                flagged[name].add((c["patient_id"], c["d"]))
            stats[name]["flags"] += len(fl)
            for f in fl:
                rid = rule_id(f)
                stats[name]["by_rule"][rid] = stats[name]["by_rule"].get(rid, 0) + 1
    gained, lost = [], []
    for e in con.execute("SELECT * FROM events").fetchall():
        start = add_days(e["date"], -lookback)
        had = any(p == e["patient_id"] and start <= d < e["date"][:10] for p, d in flagged["now"])
        has = any(p == e["patient_id"] and start <= d < e["date"][:10] for p, d in flagged["candidate"])
        item = {"event_id": e["id"], "patient_id": e["patient_id"], "date": e["date"][:10], "type": e["type"]}
        if has and not had:
            gained.append(item)
        elif had and not has:
            lost.append(item)
    n_events = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return {
        "changes": changes, "contacts": len(contacts),
        "flags_now": stats["now"]["flags"], "flags_candidate": stats["candidate"]["flags"],
        "contacts_flagged_now": stats["now"]["contacts_flagged"],
        "contacts_flagged_candidate": stats["candidate"]["contacts_flagged"],
        "extra_contacts_flagged": stats["candidate"]["contacts_flagged"] - stats["now"]["contacts_flagged"],
        "flags_by_rule_now": stats["now"]["by_rule"], "flags_by_rule_candidate": stats["candidate"]["by_rule"],
        "events": n_events,
        "events_with_prior_flag_now": sum(any(p == e["patient_id"] and add_days(e["date"], -lookback) <= d < e["date"][:10]
                                              for p, d in flagged["now"]) for e in con.execute("SELECT * FROM events")),
        "events_gaining_prior_flag": gained, "events_losing_prior_flag": lost,
        "seconds": round(time.time() - t0, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Find suspected misses and over-calls for the monthly review.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--month", default=None, help="summary for one month, YYYY-MM")
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    print("New rows:", detect(con, cfg, args.as_of))
    print(json.dumps(summary(con, cfg, args.month), indent=2))


if __name__ == "__main__":
    main()
