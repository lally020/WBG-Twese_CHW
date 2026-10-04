"""Step 3. Signals: compare a patient's blood pressure with their own baseline, their trend,
and the guideline cut-offs in config/guideline_htn.json, and return flags with reasons.
A who_risk flag is raised when the WHO 10-year CVD risk reaches a level in config.who_risk.

Usage:
    python engine/signals.py --db data/chw.db --config config/guideline_htn.json --patient 1
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import who_risk
from engine.common import action_rank, add_days, bp_series, connect, dumps, iso, load_config, loads


def slope(values):
    """Least-squares slope per reading (mmHg per reading). 0 when fewer than 2 values."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den


def latest_encounter(con, patient_id, as_of=None):
    row = con.execute(
        "SELECT * FROM encounters WHERE patient_id = ? AND date <= ? ORDER BY date DESC, id DESC LIMIT 1",
        (patient_id, iso(as_of) + "T99"),
    ).fetchone()
    return dict(row) if row else None


def patient_goal(cfg, patient):
    """The BP goal for this patient: config.bp_goal_high_risk when a supervisor or the record import set
    goal_override to high_risk, else config.bp_goal. The engine never sets goal_override itself."""
    override = patient["goal_override"] if patient is not None and "goal_override" in patient.keys() else None
    if override == "high_risk" and "bp_goal_high_risk" in cfg:
        return {**cfg["bp_goal_high_risk"], "id": "bp_goal_high_risk"}
    return {**cfg["bp_goal"], "id": "bp_goal"}


def at_goal(goal, reading):
    """True when the reading is under the goal (a missing diastolic counts as under)."""
    if not reading or reading["sbp"] is None:
        return False
    return reading["sbp"] < goal["sbp_lt"] and (reading["dbp"] is None or reading["dbp"] < goal["dbp_lt"])


def recent_symptoms(con, patient_id, reading):
    """Symptom codes (other than none) in the reading's own encounter, or in an SMS from the same patient
    in the 24 hours before it (the reading's day and the day before, since encounters carry a date only)."""
    if not reading:
        return []
    day = reading["date"][:10]
    found = []
    row = con.execute("SELECT symptom_codes FROM encounters WHERE id = ?", (reading["id"],)).fetchone()
    if row:
        found += loads(row["symptom_codes"], [])
    day_before = add_days(day, -1)
    for r in con.execute("SELECT symptom_codes FROM encounters WHERE patient_id = ? AND source = 'sms' "
                         "AND substr(date, 1, 10) BETWEEN ? AND ?", (patient_id, day_before, day)).fetchall():
        found += loads(r["symptom_codes"], [])
    for r in con.execute("SELECT parsed_json FROM messages WHERE patient_id = ? AND direction = 'in' "
                         "AND substr(received_at, 1, 10) BETWEEN ? AND ?", (patient_id, day_before, day)).fetchall():
        pj = loads(r["parsed_json"], {}) or {}
        fields = pj.get("confirmed_fields") or pj.get("fields") or {}
        found += fields.get("symptoms") or []
    return sorted({c for c in found if c and c != "none"})


def threshold_flag(cfg, reading, symptoms=()):
    """The most severe threshold the latest reading reaches, as a flag, or None.
    An entry with requires_any_symptom applies only when symptoms are present (true) or absent (false)."""
    has_symptom = bool(symptoms)
    hit = None
    for t in cfg["thresholds"]:
        sbp_hit = reading["sbp"] is not None and reading["sbp"] >= t["sbp_gte"]
        dbp_hit = reading["dbp"] is not None and reading["dbp"] >= t["dbp_gte"]
        if not (sbp_hit or dbp_hit):
            continue
        if "requires_any_symptom" in t and t["requires_any_symptom"] != has_symptom:
            continue
        if hit is None or (t["sbp_gte"], action_rank(cfg, t["action"])) > (hit["sbp_gte"], action_rank(cfg, hit["action"])):
            hit = t
    if hit is None:
        return None
    bp = f"{reading['sbp']}/{reading['dbp']}" if reading["dbp"] is not None else f"{reading['sbp']}"
    reason = (f"Latest BP {bp} on {reading['date'][:10]} is at or above the '{hit['id']}' cut-off "
              f"({hit['sbp_gte']}/{hit['dbp_gte']}).")
    if hit.get("requires_any_symptom") is True:
        reason += f" Symptom present: {', '.join(symptoms)}."
    elif hit.get("requires_any_symptom") is False:
        reason += " No symptom in the same encounter or in an SMS in the previous 24 hours."
    if hit.get("urgent_days"):
        reason += f" Urgent: within {hit['urgent_days']} days."
    flag = {
        "type": "threshold",
        "value": bp,
        "reason_text": reason,
        "guideline_ref": hit["source"],
        "emergency": "yes" if hit["action"] == "emergency_now" else "no",
        "action": hit["action"],
    }
    if hit.get("urgent_days"):
        flag["urgent_days"] = hit["urgent_days"]
    return flag


def band_floor(cfg, goal):
    """The bottom of the borderline band under the patient's goal: (sbp floor, dbp floor)."""
    band = cfg["borderline"]["band_below_goal_mmhg"]
    return goal["sbp_lt"] - band["sbp"], goal["dbp_lt"] - band["dbp"]


def _above_goal(r, goal):
    return r["sbp"] >= goal["sbp_lt"] or (r["dbp"] is not None and r["dbp"] >= goal["dbp_lt"])


def _in_band(r, cfg, goal):
    """Under the goal but within the band below it (systolic or diastolic)."""
    if _above_goal(r, goal):
        return False
    s_lo, d_lo = band_floor(cfg, goal)
    return r["sbp"] >= s_lo or (r["dbp"] is not None and r["dbp"] >= d_lo)


def borderline_flag(cfg, readings, goal, has_threshold=False):
    """PLAN.md step 3 (4 October): the patient sits just under the goal, or keeps crossing it.
    Over the last window_readings readings: consecutive_readings in a row in the band below the goal, or at
    least crossings_in_window crossings of the goal line. Never before baseline_readings + 1 readings, never
    alongside a threshold flag, never an emergency."""
    b = cfg.get("borderline")
    if not b or has_threshold or len(readings) < cfg["baseline_readings"] + 1:
        return None
    win = readings[-b["window_readings"]:]
    s_lo, d_lo = band_floor(cfg, goal)
    run = best = 0
    best_end = None
    for i, r in enumerate(win):
        run = run + 1 if _in_band(r, cfg, goal) else 0
        if run >= best:
            best, best_end = run, i
    above = [_above_goal(r, goal) for r in win]
    crossings = sum(1 for a, c in zip(above, above[1:]) if a != c)
    bp = lambda r: f"{r['sbp']}/{r['dbp']}" if r["dbp"] is not None else str(r["sbp"])  # noqa: E731
    if best >= b["consecutive_readings"]:
        run_rs = win[best_end - best + 1:best_end + 1]
        why = (f"{best} readings in a row in the band just under the goal {goal['sbp_lt']}/{goal['dbp_lt']} "
               f"({s_lo}-{goal['sbp_lt'] - 1} systolic or {d_lo}-{goal['dbp_lt'] - 1} diastolic): "
               f"{', '.join(bp(r) for r in run_rs)} ({run_rs[0]['date'][:10]} to {run_rs[-1]['date'][:10]})")
    elif crossings >= b["crossings_in_window"]:
        why = (f"the readings crossed the goal {goal['sbp_lt']}/{goal['dbp_lt']} {crossings} times in the last "
               f"{len(win)} readings: {', '.join(bp(r) for r in win)}")
    else:
        return None
    return {"type": "borderline", "value": f"{best} in band" if best >= b["consecutive_readings"] else f"{crossings} crossings",
            "reason_text": f"Borderline: {why}. No threshold crossed; recheck and ask for a weekly reading.",
            "guideline_ref": b.get("source", ""), "emergency": "no", "action": b["action"]}


def trend_flag(cfg, readings, baseline_sbp):
    """One trend flag when any config.trend rule fires. Rules never fire on a single reading:
    consecutive_readings: each of the last N readings is at least sbp_rise_mmhg above baseline;
    over_readings: the mean of the last N readings is at least sbp_rise_mmhg above baseline."""
    t = cfg["trend"]
    n_base = cfg["baseline_readings"]
    fired = []
    for rule in t["rules"]:
        if "consecutive_readings" in rule:
            n = rule["consecutive_readings"]
            if len(readings) < n_base + n:
                continue
            last = readings[-n:]
            rises = [r["sbp"] - baseline_sbp for r in last]
            if min(rises) >= rule["sbp_rise_mmhg"]:
                fired.append((min(rises), (
                    f"each of the last {n} readings ({', '.join(str(r['sbp']) for r in last)}, "
                    f"{last[0]['date'][:10]} to {last[-1]['date'][:10]}) is at least {rule['sbp_rise_mmhg']} mmHg "
                    f"above the baseline of {baseline_sbp:.0f}")))
        elif "over_readings" in rule:
            n = rule["over_readings"]
            if len(readings) < n_base + n:
                continue
            last = readings[-n:]
            mean = sum(r["sbp"] for r in last) / n
            if mean - baseline_sbp >= rule["sbp_rise_mmhg"]:
                fired.append((mean - baseline_sbp, (
                    f"the last {n} readings ({last[0]['date'][:10]} to {last[-1]['date'][:10]}) average {mean:.0f}, "
                    f"{mean - baseline_sbp:.0f} mmHg above the baseline of {baseline_sbp:.0f} "
                    f"(rule: at least {rule['sbp_rise_mmhg']} mmHg over {n} readings)")))
    if not fired:
        return None
    return {
        "type": "trend",
        "value": f"+{fired[0][0]:.0f}",
        "reason_text": "Trend rule: " + "; and ".join(r for _, r in fired) + f" (baseline = first {n_base} readings).",
        "guideline_ref": t.get("source", "local rule (physician to confirm)"),
        "emergency": "no",
        "action": t["action"],
    }


def symptom_flags(cfg, encounter, reading=None, goal=None):
    """Flags for symptoms in the latest encounter. Emergency symptoms (FAST signs) come from
    config.symptoms_emergency, at any blood pressure. Other symptoms follow config.symptoms_other:
    always_visit symptoms get visit_this_week; otherwise at_goal or above_goal by the latest reading."""
    if not encounter:
        return []
    other = cfg["symptoms_other"]
    goal = goal or {**cfg["bp_goal"], "id": "bp_goal"}
    under = at_goal(goal, reading)
    codes = [c for c in loads(encounter.get("symptom_codes"), []) if c and c != "none"]
    flags = []
    for code in codes:
        if code in cfg["symptoms_emergency"]:
            action, ref, why = "emergency_now", "config.symptoms_emergency; FAST", " - on the emergency symptom list."
        elif code in other.get("always_visit", []):
            action, ref, why = "visit_this_week", "config.symptoms_other", " - always earns a visit (it may mean low pressure from treatment)."
        else:
            action = other["at_goal"] if under else other["above_goal"]
            ref = "config.symptoms_other"
            bp = f"{reading['sbp']}/{reading['dbp']}" if reading else "no reading"
            why = (f"; latest BP {bp} is {'under' if under else 'not under'} the goal "
                   f"{goal['sbp_lt']}/{goal['dbp_lt']} ({goal['id']}).")
        flags.append({
            "type": "symptom",
            "value": code,
            "reason_text": f"Symptom '{code}' reported on {encounter['date'][:10]}{why}",
            "guideline_ref": ref,
            "emergency": "yes" if action == "emergency_now" else "no",
            "action": action,
        })
    return flags


def compute(con, cfg, patient_id, as_of=None):
    """Baseline, change, slope and flags for one patient, as of a date. Writes nothing."""
    readings = bp_series(con, patient_id, as_of)
    n_base = cfg["baseline_readings"]
    patient = con.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    goal = patient_goal(cfg, patient)
    out = {
        "patient_id": patient_id,
        "as_of": iso(as_of),
        "n_readings": len(readings),
        "baseline_sbp": None,
        "baseline_dbp": None,
        "latest": None,
        "change_sbp": None,
        "slope_sbp": None,
        "goal": goal,
        "flags": [],
    }
    last = None
    if readings:
        base = readings[:n_base]
        out["baseline_sbp"] = round(sum(r["sbp"] for r in base) / len(base), 1)
        dbps = [r["dbp"] for r in base if r["dbp"] is not None]
        out["baseline_dbp"] = round(sum(dbps) / len(dbps), 1) if dbps else None
        last = readings[-1]
        out["latest"] = {"date": last["date"][:10], "sbp": last["sbp"], "dbp": last["dbp"]}
        out["change_sbp"] = round(last["sbp"] - out["baseline_sbp"], 1)
        out["slope_sbp"] = round(slope([r["sbp"] for r in readings[-cfg["trend_window_readings"]:]]), 2)

        f = threshold_flag(cfg, last, recent_symptoms(con, patient_id, last))
        if f:
            out["flags"].append(f)
        f = who_risk.flag(cfg, patient, last["sbp"], last["date"]) if patient else None
        if f:
            out["flags"].append(f)
        if len(readings) > n_base:
            f = trend_flag(cfg, readings, out["baseline_sbp"])
            if f:
                out["flags"].append(f)
        f = borderline_flag(cfg, readings, goal, any(x["type"] == "threshold" for x in out["flags"]))
        if f:
            out["flags"].append(f)
    out["flags"] += symptom_flags(cfg, latest_encounter(con, patient_id, as_of), last, goal)
    return out


def write_flags(con, patient_id, as_of, flags):
    """Replace this patient's flags for this date with the new ones."""
    day = iso(as_of)
    con.execute("DELETE FROM flags WHERE patient_id = ? AND date = ?", (patient_id, day))
    for f in flags:
        con.execute(
            "INSERT INTO flags (patient_id, date, type, value, reason_text, guideline_ref, emergency) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (patient_id, day, f["type"], f["value"], f["reason_text"], f["guideline_ref"], f["emergency"]),
        )


def run(con, cfg, patient_id, as_of=None, write=True):
    """Compute the signals and (by default) store the flags. Returns the signals dict."""
    result = compute(con, cfg, patient_id, as_of)
    if write:
        write_flags(con, patient_id, as_of, result["flags"])
        con.commit()
    return result


def open_flags(con, patient_id):
    """The flags from the most recent run for this patient."""
    row = con.execute("SELECT MAX(date) AS d FROM flags WHERE patient_id = ?", (patient_id,)).fetchone()
    last_run = con.execute(
        "SELECT value FROM meta WHERE key = ?", (f"signals_run:{patient_id}",)
    ).fetchone()
    if not row or not row["d"]:
        return []
    # If signals ran later than the last flag date and found nothing, there are no open flags.
    if last_run and last_run["value"] > row["d"]:
        return []
    rows = con.execute(
        "SELECT * FROM flags WHERE patient_id = ? AND date = ? ORDER BY emergency DESC, id",
        (patient_id, row["d"]),
    ).fetchall()
    return [dict(r) for r in rows]


def refresh(con, cfg, patient_id, as_of=None):
    """Run the signals, store the flags, and remember when they ran."""
    result = run(con, cfg, patient_id, as_of, write=True)
    con.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (f"signals_run:{patient_id}", iso(as_of)),
    )
    con.commit()
    return result


def main():
    parser = argparse.ArgumentParser(description="Compute blood pressure flags for one patient.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--patient", type=int, required=True)
    parser.add_argument("--date", default=None, help="as-of date, YYYY-MM-DD (default today)")
    parser.add_argument("--no-write", action="store_true", help="print only, do not store flags")
    args = parser.parse_args()

    con = connect(args.db)
    cfg = load_config(args.config)
    if args.no_write:
        result = compute(con, cfg, args.patient, args.date)
    else:
        result = refresh(con, cfg, args.patient, args.date)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["flags"]:
        print("No flags.")


if __name__ == "__main__":
    main()
