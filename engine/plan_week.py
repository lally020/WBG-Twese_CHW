"""Step 9. The weekly CHW plan: put the hours where the risk is highest.

1. Patients on the escalation list are left out: the emergency protocol handles them, not the plan.
2. Each patient's estimated minutes come from time_log (average visit and travel minutes per village),
   with config.capacity defaults as fallback.
3. Visits go to medium and high tier patients by score per estimated minute, until the hours run out.
   Once a village is on the route, more visits there cost no extra travel, so visits group by village.
4. SMS check-ins for the next patients with any risk points; everyone else waits.
A person approves every plan (approve_plan).

Usage:
    python engine/plan_week.py --db data/chw.db --config config/guideline_htn.json --chw 1 --week-start 2026-10-05 --hours 20
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import signals
from engine.common import audit, connect, get_meta, iso, load_config, loads, now_iso, set_meta
from engine.hours import village_minutes


def escalation_ids(con):
    """Active patients with an open emergency flag, or whose latest decision is emergency_now.
    Two SQL queries for the whole clinic, not one per patient (one laptop, many thousand patients).
    Open flags = the flags of the patient's latest flag date, unless signals ran later and found none."""
    flagged = con.execute(
        "SELECT DISTINCT f.patient_id FROM flags f "
        "JOIN (SELECT patient_id, MAX(date) AS d FROM flags GROUP BY patient_id) m "
        "  ON m.patient_id = f.patient_id AND m.d = f.date "
        "JOIN patients p ON p.id = f.patient_id AND p.status = 'active' "
        "LEFT JOIN meta r ON r.key = 'signals_run:' || f.patient_id "
        "WHERE f.emergency = 'yes' AND (r.value IS NULL OR r.value <= m.d)").fetchall()
    decided = con.execute(
        "SELECT d.patient_id FROM decisions d "
        "JOIN (SELECT patient_id, MAX(date) AS md FROM decisions GROUP BY patient_id) l "
        "  ON l.patient_id = d.patient_id AND l.md = d.date "
        "JOIN patients p ON p.id = d.patient_id AND p.status = 'active' "
        "WHERE d.id = (SELECT MAX(id) FROM decisions x WHERE x.patient_id = d.patient_id AND x.date = l.md) "
        "AND COALESCE(d.final_choice, d.suggestion) = 'emergency_now'").fetchall()
    return {r[0] for r in flagged} | {r[0] for r in decided}


def latest_scores(con, chw_id=None):
    """The latest risk score per patient (only this CHW's patients when chw_id is given)."""
    sql = ("SELECT r.* FROM risk_scores r "
           "WHERE r.date = (SELECT MAX(date) FROM risk_scores x WHERE x.patient_id = r.patient_id)")
    params = ()
    if chw_id is not None:
        sql += " AND r.patient_id IN (SELECT id FROM patients WHERE chw_id = ? AND status = 'active')"
        params = (chw_id,)
    rows = con.execute(sql, params).fetchall()
    return {r["patient_id"]: dict(r) for r in rows}


def _top_reasons(components_json, n=2):
    comp = loads(components_json, {}) or {}
    items = sorted(((k, v) for k, v in comp.items() if v.get("points")), key=lambda kv: -kv[1]["points"])
    return ", ".join(k.replace("_", " ") for k, _ in items[:n])


def build_plan(con, cfg, hours, chw_id=1):
    """Decide visit / sms / wait for every patient. Returns rows (not stored) and minutes used."""
    cap = cfg["capacity"]
    budget = hours * 60
    vm = village_minutes(con, cfg, chw_id)
    scores = latest_scores(con, chw_id)
    skip = escalation_ids(con)
    patients = {r["id"]: dict(r) for r in con.execute(
        "SELECT id, village FROM patients WHERE status = 'active' AND chw_id = ?", (chw_id,)).fetchall()}

    cands = []
    for pid, p in patients.items():
        if pid in skip or pid not in scores:
            continue
        s = scores[pid]
        m = vm.get(p["village"], {"visit": cap["default_visit_minutes"], "travel": cap["default_travel_minutes"]})
        cands.append({"patient_id": pid, "village": p["village"], "score": s["score"], "tier": s["tier"],
                      "visit_min": m["visit"], "travel_min": m["travel"], "why": _top_reasons(s["components_json"])})

    remaining = budget
    opened = []  # villages on this week's route, in the order they were added
    visits = []
    eligible = [c for c in cands if c["tier"] in ("high", "medium")]
    while eligible:
        for c in eligible:
            c["cost"] = c["visit_min"] + (0 if c["village"] in opened else c["travel_min"])
        best = max(eligible, key=lambda c: (c["score"] / c["cost"], c["score"]))
        eligible.remove(best)
        if best["cost"] > remaining:
            best["no_time"] = True
            continue
        remaining -= best["cost"]
        if best["village"] not in opened:
            opened.append(best["village"])
        best["action"] = "visit"
        best["est_minutes"] = round(best["cost"], 1)
        travel_note = "travel shared with other visits there" if best["cost"] == best["visit_min"] else \
            f"incl. {best['travel_min']:.0f} min travel"
        best["reason_text"] = (f"Visit: {best['tier']} tier, score {best['score']:g} ({best['why']}). "
                               f"{best['village']} route, about {best['cost']:.0f} min, {travel_note}.")
        visits.append(best)

    rest = sorted([c for c in cands if c.get("action") is None], key=lambda c: -c["score"])
    for c in rest:
        if c["score"] > 0 and remaining >= cap["sms_minutes"]:
            remaining -= cap["sms_minutes"]
            c["action"], c["est_minutes"] = "sms", cap["sms_minutes"]
            lead = "No hours left for a visit. " if c.get("no_time") else ""
            c["reason_text"] = f"{lead}SMS check-in: score {c['score']:g} ({c['why']}), {cap['sms_minutes']} min."
        else:
            c["action"], c["est_minutes"] = "wait", 0
            c["reason_text"] = ("Wait: no flags or open items this week; routine schedule." if c["score"] <= 0
                                else "Wait: no hours left this week.")

    # Rank: visits in priority order, then SMS, then wait. Display order groups visits by village.
    ordered = visits + [c for c in rest if c["action"] == "sms"] + [c for c in rest if c["action"] == "wait"]
    for i, c in enumerate(ordered, 1):
        c["rank"] = i
    return ordered, budget - remaining, sorted(skip), opened


def get_plan(con, cfg, chw_id, week_start):
    """The stored plan for this CHW and week, with hours used and left."""
    week_start = iso(week_start)
    rows = [dict(r) for r in con.execute(
        "SELECT pl.*, p.village, p.age, p.sex, r.score, r.tier FROM plans pl JOIN patients p ON p.id = pl.patient_id "
        "LEFT JOIN risk_scores r ON r.patient_id = pl.patient_id "
        "AND r.date = (SELECT MAX(date) FROM risk_scores x WHERE x.patient_id = pl.patient_id) "
        "WHERE pl.chw_id = ? AND pl.week_start = ? ORDER BY pl.rank",
        (chw_id, week_start)).fetchall()]
    if not rows:
        return None
    hours = float(get_meta(con, f"plan_hours:{chw_id}:{week_start}", cfg["capacity"]["hours_per_week"]))
    used = sum(r["est_minutes"] or 0 for r in rows)
    approved = all(r["confirmed"] for r in rows)
    # Visits grouped by village, in route order, for the frontend.
    route = []
    for r in rows:
        if r["action"] == "visit" and r["village"] not in route:
            route.append(r["village"])
    by_village = [{"village": v, "patient_ids": [r["patient_id"] for r in rows
                                                 if r["action"] == "visit" and r["village"] == v]} for v in route]
    return {
        "chw_id": chw_id, "week_start": week_start, "hours": hours,
        "minutes_used": round(used), "minutes_left": round(hours * 60 - used),
        "hours_used": round(used / 60, 1), "hours_left": round(hours - used / 60, 1),
        "approved": approved, "approved_at": rows[0]["confirmed"] if approved else None,
        "counts": {a: sum(r["action"] == a for r in rows) for a in ("visit", "sms", "wait")},
        "route": by_village,
        "rows": rows,
        "escalation_patient_ids": sorted(escalation_ids(con)),
    }


def plan_week(con, cfg, chw_id, week_start, hours=None, regenerate=False):
    """Make (or return) the plan. A stored plan is kept unless the hours change or regenerate is asked.
    An approved plan is never overwritten unless regenerate is True."""
    week_start = iso(week_start)
    hours = float(hours if hours is not None else cfg["capacity"]["hours_per_week"])
    existing = get_plan(con, cfg, chw_id, week_start)
    if existing and not regenerate:
        if existing["approved"] or existing["hours"] == hours:
            return existing
    if not latest_scores(con, chw_id):
        from engine import risk
        risk.risk_all(con, cfg)
    rows, used, skipped, route = build_plan(con, cfg, hours, chw_id)
    con.execute("DELETE FROM plans WHERE chw_id = ? AND week_start = ?", (chw_id, week_start))
    for r in rows:
        con.execute(
            "INSERT INTO plans (chw_id, week_start, patient_id, action, rank, est_minutes, reason_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chw_id, week_start, r["patient_id"], r["action"], r["rank"], r["est_minutes"], r["reason_text"]),
        )
    set_meta(con, f"plan_hours:{chw_id}:{week_start}", hours)
    con.commit()
    return get_plan(con, cfg, chw_id, week_start)


def update_plan(con, cfg, plan_id, action):
    """The CHW moves one patient between visit, sms and wait."""
    if action not in ("visit", "sms", "wait"):
        return {"error": "action must be visit, sms or wait"}
    row = con.execute("SELECT pl.*, p.village FROM plans pl JOIN patients p ON p.id = pl.patient_id WHERE pl.id = ?",
                      (plan_id,)).fetchone()
    if row is None:
        return {"error": f"plan row {plan_id} not found"}
    cap = cfg["capacity"]
    m = village_minutes(con, cfg).get(row["village"], {"visit": cap["default_visit_minutes"],
                                                       "travel": cap["default_travel_minutes"]})
    est = {"visit": m["visit"] + m["travel"], "sms": cap["sms_minutes"], "wait": 0}[action]
    reason = (row["reason_text"] or "").split(" [Changed by CHW")[0] + f" [Changed by CHW from {row['action']} to {action}.]"
    con.execute("UPDATE plans SET action = ?, est_minutes = ?, reason_text = ?, confirmed = NULL WHERE id = ?",
                (action, round(est, 1), reason, plan_id))
    # Any change re-opens the plan for approval.
    con.execute("UPDATE plans SET confirmed = NULL WHERE chw_id = ? AND week_start = ?", (row["chw_id"], row["week_start"]))
    audit(con, "update_plan", "plans", plan_id)
    con.commit()
    return get_plan(con, cfg, row["chw_id"], row["week_start"])


def approve_plan(con, cfg, chw_id, week_start, user_id="chw"):
    week_start = iso(week_start)
    n = con.execute("UPDATE plans SET confirmed = ? WHERE chw_id = ? AND week_start = ?",
                    (now_iso(), chw_id, week_start)).rowcount
    if n == 0:
        return {"error": "no plan for this CHW and week"}
    audit(con, "approve_plan", "plans", None, user_id=user_id)
    con.commit()
    return get_plan(con, cfg, chw_id, week_start)


def main():
    parser = argparse.ArgumentParser(description="Make the weekly CHW plan.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--chw", type=int, required=True)
    parser.add_argument("--week-start", required=True)
    parser.add_argument("--hours", type=float, default=None)
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    plan = plan_week(con, cfg, args.chw, args.week_start, args.hours, regenerate=True)
    print(f"Week of {plan['week_start']}, CHW {plan['chw_id']}: {plan['hours']:g} hours, "
          f"{plan['hours_used']} used, {plan['hours_left']} left. {plan['counts']}")
    print(f"Escalation list (not planned): {plan['escalation_patient_ids']}")
    for v in plan["route"]:
        print(f"  {v['village']}: patients {v['patient_ids']}")
    for r in plan["rows"]:
        if r["action"] != "wait":
            print(f"  #{r['rank']:>2} patient {r['patient_id']:>3} {r['action']:<5} {r['est_minutes']:>5} min  {r['reason_text']}")


if __name__ == "__main__":
    main()
