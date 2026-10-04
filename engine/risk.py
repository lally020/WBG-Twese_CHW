"""Step 8. Risk score per patient.

score = WHO 10-year CVD risk in percent x score_per_percent (engine/who_risk.py, Eastern Sub-Saharan Africa)
      + points from config.risk.points for: each active flag (threshold, trend, symptom), missed medication,
        each full 30 days of silence, an overdue follow-up, a missed clinic appointment, low quiz understanding,
        and a peer-trajectory flag (config.trajectory.points).
tier  = high / medium / low from config.risk.tiers.
When the WHO table file is missing, the base is 0 and a warning says so (points only).

Usage:
    python engine/risk.py --db data/chw.db --config config/guideline_htn.json [--date 2026-10-03]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import signals, who_risk
from engine.common import connect, days_between, dumps, iso, last_contact_date, load_config, root_path

_warned = {"table": False}


def load_base_table(cfg):
    """The WHO chart (kept for older callers; see engine/who_risk.py)."""
    return who_risk.load_table(cfg)


def understanding_low(con, cfg, patient_id, as_of):
    """True when danger_signs or medication understanding is below the cut-off."""
    from engine.decide import _understanding
    u = _understanding(con, cfg, patient_id, as_of)
    cut = cfg["quiz"]["low_understanding_below"]
    return [t for t in ("danger_signs", "medication") if u.get(t) is not None and u[t] < cut]


def score_patient(con, cfg, patient_id, as_of=None, table=None, sig=None):
    """The score, tier and components for one patient. Writes nothing."""
    as_of = iso(as_of)
    pts = cfg["risk"]["points"]
    patient = con.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    sig = sig or signals.compute(con, cfg, patient_id, as_of)
    comp = {}

    latest_sbp = sig["latest"]["sbp"] if sig["latest"] else None
    w = who_risk.lookup(cfg, patient, latest_sbp)
    comp["who_base"] = {"points": round(w.get("points", 0.0), 1), "percent_10y": w["percent"],
                        "reasons": [f"WHO 10-year CVD risk {w['percent']}% ({w['description']})."
                                    + (f" Note: {w['age_note']}." if w.get("age_note") else "")]
                        if w["percent"] is not None else [],
                        "note": None if w["percent"] is not None else w["reason"]}

    for f in sig["flags"]:
        key = f"{f['type']}_flag"
        if key in pts:
            comp.setdefault(key, {"points": 0, "reasons": []})
            comp[key]["points"] += pts[key]
            comp[key]["reasons"].append(f["reason_text"])

    last = signals.latest_encounter(con, patient_id, as_of)
    if last and last["meds_taken"] in ("no", "partial"):
        comp["missed_meds"] = {"points": pts["missed_meds"], "reasons": [f"Medicine taken: {last['meds_taken']} "
                                                                         f"on {last['date'][:10]}."]}

    contact = last_contact_date(con, patient_id, as_of)
    silent_days = days_between(contact, as_of) if contact else None
    if silent_days is not None and silent_days >= 30:
        periods = silent_days // 30
        comp["silent_30_days"] = {"points": pts["silent_30_days"] * periods,
                                  "reasons": [f"No contact for {silent_days} days."]}

    overdue = con.execute(
        "SELECT COUNT(*) FROM followups WHERE patient_id = ? AND kind != 'clinic_appointment' "
        "AND due_date < ? AND done_on IS NULL", (patient_id, as_of)
    ).fetchone()[0]
    if overdue:
        comp["overdue_followup"] = {"points": pts["overdue_followup"], "reasons": [f"{overdue} overdue follow-up(s)."]}

    # A missed clinic appointment since the last attended one.
    last_done = con.execute(
        "SELECT MAX(due_date) FROM followups WHERE patient_id = ? AND kind = 'clinic_appointment' "
        "AND done_on IS NOT NULL AND due_date <= ?", (patient_id, as_of)
    ).fetchone()[0] or ""
    missed = con.execute(
        "SELECT COUNT(*) FROM followups WHERE patient_id = ? AND kind = 'clinic_appointment' "
        "AND done_on IS NULL AND due_date < ? AND due_date > ?", (patient_id, as_of, last_done)
    ).fetchone()[0]
    if missed:
        comp["missed_appointment"] = {"points": pts["missed_appointment"],
                                      "reasons": [f"{missed} missed clinic appointment(s) since the last visit."]}

    low = understanding_low(con, cfg, patient_id, as_of)
    if low:
        comp["low_understanding"] = {"points": pts["low_understanding"],
                                     "reasons": [f"Low understanding: {', '.join(low)}."]}

    traj = con.execute(
        "SELECT flag, reason_text FROM trajectory_scores WHERE patient_id = ? AND date <= ? "
        "ORDER BY date DESC, id DESC LIMIT 1", (patient_id, as_of)
    ).fetchone()
    if traj and traj["flag"] == "yes":
        comp["trajectory"] = {"points": cfg["trajectory"]["points"], "reasons": [traj["reason_text"]]}

    score = round(sum(c["points"] for c in comp.values()), 1)
    tiers = cfg["risk"]["tiers"]
    tier = "high" if score >= tiers["high"] else "medium" if score >= tiers["medium"] else "low"
    return {"patient_id": patient_id, "date": as_of, "score": score, "tier": tier, "components": comp,
            "emergency": any(f["emergency"] == "yes" for f in sig["flags"]),
            "config_version": cfg["config_version"]}


def write_score(con, r):
    con.execute("DELETE FROM risk_scores WHERE patient_id = ? AND date = ?", (r["patient_id"], r["date"]))
    con.execute(
        "INSERT INTO risk_scores (patient_id, date, score, tier, components_json, config_version) VALUES (?, ?, ?, ?, ?, ?)",
        (r["patient_id"], r["date"], r["score"], r["tier"], dumps(r["components"]), r["config_version"]),
    )


def score_one(con, cfg, patient_id, as_of=None):
    """Score and store one patient (used after an SMS is confirmed)."""
    table = load_base_table(cfg)
    r = score_patient(con, cfg, patient_id, as_of, table)
    write_score(con, r)
    con.commit()
    return r


def risk_all(con, cfg, as_of=None):
    """Score every hypertension patient, store the scores, return them highest first."""
    table = load_base_table(cfg)
    if table is None and not _warned["table"]:
        print(f"Warning: {cfg['risk']['base_table']} not found. The score uses points only.", file=sys.stderr)
        _warned["table"] = True
    out = []
    for (pid,) in con.execute("SELECT id FROM patients WHERE status = 'active' AND condition_codes LIKE '%hypertension%'").fetchall():
        r = score_patient(con, cfg, pid, as_of, table)
        write_score(con, r)
        out.append(r)
    con.commit()
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def main():
    parser = argparse.ArgumentParser(description="Score every patient.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    from engine import trajectory
    trajectory.score_all(con, cfg, args.date)  # the peer-trajectory flag adds points (off until enough events)
    scores = risk_all(con, cfg, args.date)
    tiers = {t: sum(r["tier"] == t for r in scores) for t in ("high", "medium", "low")}
    print(f"Scored {len(scores)} patients: {tiers}")
    print("Top 10:")
    for r in scores[:10]:
        parts = ", ".join(f"{k} {v['points']}" for k, v in r["components"].items() if v["points"])
        print(f"  patient {r['patient_id']:>3}  score {r['score']:>5}  {r['tier']:<6} {'EMERGENCY ' if r['emergency'] else ''}{parts}")


if __name__ == "__main__":
    main()
