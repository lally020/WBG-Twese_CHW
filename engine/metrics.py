"""Step 11. Efficacy metrics for one month ('YYYY-MM').

Usage:
    python engine/metrics.py --db data/chw.db --config config/guideline_htn.json --month 2026-09
"""

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import connect, days_between, load_config, loads
from engine.signals import at_goal as at_goal_check, patient_goal
from engine.hours import minutes


def _bounds(month):
    y, m = int(month[:4]), int(month[5:7])
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return start.isoformat(), end.isoformat(), (end - start).days / 7


def _share(n, d):
    return round(n / d, 3) if d else None


def metrics(con, cfg, month):
    start, end, weeks = _bounds(month)
    out = {"month": month, "config_version": cfg["config_version"]}

    # Suggestions and the CHW's choices.
    dec = con.execute("SELECT suggestion, final_choice FROM decisions WHERE date >= ? AND date < ?", (start, end)).fetchall()
    confirmed = [d for d in dec if d["final_choice"]]
    from engine.common import action_rank
    ups = sum(d["final_choice"] != d["suggestion"] and action_rank(cfg, d["final_choice"]) > action_rank(cfg, d["suggestion"])
              for d in confirmed)
    downs = sum(d["final_choice"] != d["suggestion"] and action_rank(cfg, d["final_choice"]) < action_rank(cfg, d["suggestion"])
                for d in confirmed)
    out["suggestions"] = {
        "made": len(dec),
        "confirmed": len(confirmed),
        "accepted": sum(d["final_choice"] == d["suggestion"] for d in confirmed),
        "override_rate": _share(sum(d["final_choice"] != d["suggestion"] for d in confirmed), len(confirmed)),
        "override_up_rate": _share(ups, len(confirmed)),
        "override_down_rate": _share(downs, len(confirmed)),
        "not_sure_rate": _share(sum(d["suggestion"] == "not_sure" for d in dec), len(dec)),
    }

    # Days from a flag to the next CHW contact.
    gaps = []
    for f in con.execute("SELECT patient_id, MIN(date) AS d FROM flags WHERE date >= ? AND date < ? "
                         "GROUP BY patient_id", (start, end)).fetchall():
        nxt = con.execute("SELECT MIN(date) FROM encounters WHERE patient_id = ? AND source = 'chw' AND date >= ?",
                          (f["patient_id"], f["d"])).fetchone()[0]
        if nxt:
            gaps.append(days_between(f["d"], nxt))
    out["days_flag_to_contact"] = {"mean": round(sum(gaps) / len(gaps), 1) if gaps else None, "n": len(gaps)}

    # Follow-ups.
    fu = con.execute("SELECT status, kind FROM followups WHERE due_date >= ? AND due_date < ?", (start, end)).fetchall()
    out["followups"] = {"due": len(fu), "done": sum(f["status"] == "done" for f in fu),
                        "missed": sum(f["status"] == "missed" for f in fu),
                        "missed_clinic_appointments": sum(f["status"] == "missed" and f["kind"] == "clinic_appointment"
                                                          for f in fu)}
    out["overdue_now"] = con.execute("SELECT COUNT(*) FROM followups WHERE done_on IS NULL AND due_date < date('now')"
                                     ).fetchone()[0]

    # Hours.
    tl = con.execute('SELECT activity, patient_id, start, "end" FROM time_log WHERE start >= ? AND start < ?',
                     (start, end)).fetchall()
    total = sum(minutes(t["start"], t["end"]) for t in tl)
    out["hours_per_week"] = round(total / 60 / weeks, 1)
    out["hours_by_activity"] = {a: round(sum(minutes(t["start"], t["end"]) for t in tl if t["activity"] == a) / 60, 1)
                                for a in ("visit", "travel", "sms", "admin")}

    # Planned visits completed.
    pv = con.execute("SELECT done_on FROM plans WHERE action = 'visit' AND week_start >= ? AND week_start < ?",
                     (start, end)).fetchall()
    out["planned_visits"] = {"planned": len(pv), "completed": sum(1 for p in pv if p["done_on"]),
                             "share": _share(sum(1 for p in pv if p["done_on"]), len(pv))}

    # Share of visits that went to high-tier patients (tier from the latest score).
    tiers = {r["patient_id"]: r["tier"] for r in con.execute(
        "SELECT patient_id, tier, MAX(date) FROM risk_scores GROUP BY patient_id").fetchall()}
    visits = [t for t in tl if t["activity"] == "visit" and t["patient_id"]]
    out["share_visits_high_tier"] = _share(sum(tiers.get(v["patient_id"]) == "high" for v in visits), len(visits))

    # Patient understanding.
    q = con.execute("SELECT patient_id, topic, correct FROM quiz_results WHERE correct IN ('yes','no') "
                    "AND asked_at >= ? AND asked_at < ?", (start, end)).fetchall()
    out["understanding_by_topic"] = {t: _share(sum(r["correct"] == "yes" for r in q if r["topic"] == t),
                                               sum(r["topic"] == t for r in q)) for t in cfg["quiz"]["topics"]}
    ds = {}
    for r in q:
        if r["topic"] == "danger_signs":
            ds.setdefault(r["patient_id"], []).append(r["correct"] == "yes")
    know = sum(sum(v) / len(v) >= cfg["quiz"]["low_understanding_below"] for v in ds.values())
    out["share_patients_know_danger_signs"] = {"share": _share(know, len(ds)), "asked": len(ds)}

    # Blood pressure: at goal, and change from baseline, on each patient's latest reading this month.
    at_goal, changes, n, high_risk = 0, [], 0, 0
    for (pid,) in con.execute("SELECT DISTINCT patient_id FROM encounters WHERE sbp IS NOT NULL AND date >= ? AND date < ?",
                              (start, end)).fetchall():
        rs = con.execute("SELECT sbp, dbp, date FROM encounters WHERE patient_id = ? AND sbp IS NOT NULL AND date < ? "
                         "ORDER BY date, id", (pid, end)).fetchall()
        base = rs[:cfg["baseline_readings"]]
        last = rs[-1]
        n += 1
        # Each patient's own goal: the high-risk goal when a supervisor or the import set goal_override.
        goal = patient_goal(cfg, con.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone())
        high_risk += goal["id"] == "bp_goal_high_risk"
        at_goal += at_goal_check(goal, last)
        changes.append(last["sbp"] - sum(r["sbp"] for r in base) / len(base))
    out["bp"] = {"patients_with_reading": n, "at_goal": at_goal, "share_at_goal": _share(at_goal, n),
                 "mean_change_from_baseline_sbp": round(sum(changes) / len(changes), 1) if changes else None,
                 "goal": cfg["bp_goal"], "goal_high_risk": cfg.get("bp_goal_high_risk"),
                 "patients_on_high_risk_goal": high_risk}

    # Referrals completed: a referral at an encounter, followed by a clinic encounter.
    refs = con.execute("SELECT patient_id, date, referral FROM encounters WHERE date >= ? AND date < ? "
                       "AND referral IS NOT NULL AND referral != '[]'", (start, end)).fetchall()
    done = 0
    for r in refs:
        if loads(r["referral"], []):
            done += bool(con.execute("SELECT 1 FROM encounters WHERE patient_id = ? AND source = 'clinic' AND date > ?",
                                     (r["patient_id"], r["date"])).fetchone())
    out["referrals"] = {"made": len(refs), "completed": done}
    out["events"] = con.execute("SELECT COUNT(*) FROM events WHERE date >= ? AND date < ?", (start, end)).fetchone()[0]
    # Step 11c: misses and over-calls.
    from engine import errors
    lookback = cfg.get("error_review", {}).get("event_lookback_days", 90)
    evs = con.execute("SELECT * FROM events WHERE date >= ? AND date < ?", (start, end)).fetchall()
    out["events_with_prior_flag"] = {"events": len(evs), "with_prior_flag": sum(
        bool(errors.prior_flags(con, cfg, e["patient_id"], e["date"][:10], lookback)[1]) for e in evs)}
    out["low_yield_visits"] = len(errors.low_yield_visits(con, cfg, start, end))
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'error_reviews'").fetchone():
        er = con.execute("SELECT kind, supervisor_label FROM error_reviews WHERE detected_on >= ? AND detected_on < ?",
                         (start, end)).fetchall()
        out["error_reviews"] = {"detected": len(er),
                                "by_kind": {k: sum(r["kind"] == k for r in er) for k in
                                            ("false_positive", "false_negative", "extraction_error")},
                                "pending": sum(r["supervisor_label"] == "pending" for r in er),
                                "confirmed_false_positives": sum(r["kind"] == "false_positive" and
                                                                 r["supervisor_label"] == "confirmed_error" for r in er),
                                "confirmed_false_negatives": sum(r["kind"] == "false_negative" and
                                                                 r["supervisor_label"] == "confirmed_error" for r in er)}
    return out


def main():
    parser = argparse.ArgumentParser(description="Monthly efficacy metrics.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    args = parser.parse_args()
    con = connect(args.db)
    print(json.dumps(metrics(con, load_config(args.config), args.month), indent=2))


if __name__ == "__main__":
    main()
