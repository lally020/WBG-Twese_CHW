"""Step 7. Where the CHW's hours go.

- log_time writes one row to time_log, with the patient's village.
- hours(chw_id, week_start) returns the weekly table per activity, and the average visit and
  travel minutes per village (which the weekly plan uses).

Usage:
    python engine/hours.py --db data/chw.db --config config/guideline_htn.json --chw 1 --week-start 2026-09-28 [--csv out.csv]
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import add_days, audit, connect, iso, load_config

ACTIVITIES = ("visit", "sms", "travel", "admin")


def minutes(start, end):
    try:
        return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60)
    except (TypeError, ValueError):
        return 0.0


def log_time(con, chw_id, activity, patient_id=None, start=None, end=None):
    if activity not in ACTIVITIES:
        return {"error": f"activity must be one of {ACTIVITIES}"}
    village = None
    if patient_id:
        row = con.execute("SELECT village FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if row is None:
            return {"error": f"patient {patient_id} not found"}
        village = row["village"]
    if minutes(start, end) <= 0:
        return {"error": "end must be after start (ISO times, for example 2026-10-03T09:00:00)"}
    cur = con.execute(
        'INSERT INTO time_log (chw_id, activity, patient_id, village, start, "end") VALUES (?, ?, ?, ?, ?, ?)',
        (chw_id, activity, patient_id, village, start, end),
    )
    audit(con, "log_time", "time_log", cur.lastrowid)
    con.commit()
    return {"id": cur.lastrowid, "chw_id": chw_id, "activity": activity, "patient_id": patient_id,
            "village": village, "minutes": round(minutes(start, end), 1)}


def village_minutes(con, cfg, chw_id=None):
    """Average visit and travel minutes per village from time_log, with config defaults as fallback."""
    cap = cfg["capacity"]
    sql = 'SELECT village, activity, start, "end" FROM time_log WHERE village IS NOT NULL AND activity IN (\'visit\', \'travel\')'
    params = ()
    if chw_id is not None:
        sql += " AND chw_id = ?"
        params = (chw_id,)
    sums = {}
    for r in con.execute(sql, params).fetchall():
        s = sums.setdefault(r["village"], {"visit": [], "travel": []})
        s[r["activity"]].append(minutes(r["start"], r["end"]))
    out = {}
    for v, s in sums.items():
        out[v] = {
            "visit": round(sum(s["visit"]) / len(s["visit"]), 1) if s["visit"] else cap["default_visit_minutes"],
            "travel": round(sum(s["travel"]) / len(s["travel"]), 1) if s["travel"] else cap["default_travel_minutes"],
            "n_visits": len(s["visit"]),
        }
    return out


def hours(con, cfg, chw_id, week_start):
    """The weekly table per activity, the capacity, and the per-village averages."""
    week_start = iso(week_start)
    week_end = add_days(week_start, 7)
    rows = con.execute(
        'SELECT activity, start, "end" FROM time_log WHERE chw_id = ? AND start >= ? AND start < ?',
        (chw_id, week_start, week_end),
    ).fetchall()
    table = {a: {"minutes": 0.0, "count": 0} for a in ACTIVITIES}
    for r in rows:
        table[r["activity"]]["minutes"] += minutes(r["start"], r["end"])
        table[r["activity"]]["count"] += 1
    total = sum(t["minutes"] for t in table.values())
    return {
        "chw_id": chw_id,
        "week_start": week_start,
        "by_activity": [{"activity": a, "minutes": round(t["minutes"]), "hours": round(t["minutes"] / 60, 1),
                         "count": t["count"]} for a, t in table.items()],
        "total_hours": round(total / 60, 1),
        "capacity_hours": cfg["capacity"]["hours_per_week"],
        "villages": [{"village": v, **m} for v, m in sorted(village_minutes(con, cfg, chw_id).items())],
    }


def main():
    parser = argparse.ArgumentParser(description="Weekly hours table for one CHW.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--chw", type=int, required=True)
    parser.add_argument("--week-start", required=True)
    parser.add_argument("--csv", default=None, help="write the per-activity table to this CSV file")
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    out = hours(con, cfg, args.chw, args.week_start)
    print(json.dumps(out, indent=2))
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["activity", "minutes", "hours", "count"])
            w.writeheader()
            w.writerows(out["by_activity"])
        print(f"Wrote {args.csv}")


if __name__ == "__main__":
    main()
