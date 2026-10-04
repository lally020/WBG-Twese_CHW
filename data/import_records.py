"""Step 6. Import existing records from a CSV into patients and encounters.

One row per encounter. Columns (the existing record fields, PLAN.md section 4):
    patient_ref, sex, age, number_of_children, smoker, height_cm, weight_kg, village, language,
    date, source, complaint, tests_performed, positive_results, referral, doctor_recommendation,
    doctor_notes, chw_notes, bp, blood_sugar, blood_sugar_unit
Optional column: goal_override, empty or "high_risk" (the clinician's decision that this patient's BP goal
is config.bp_goal_high_risk). The engine never sets it; only this import or a supervisor with a PIN.
Multi-choice fields are separated by ';' (for example "bp;blood_sugar").
bp is split into sbp and dbp ("140/90"); a single number is stored as sbp only.
positive_results become the patient's condition codes.

Usage:
    python data/import_records.py --db data/chw.db --csv data/sample_import.csv
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import audit, connect, get_meta, set_meta


def split_list(text):
    return [x.strip().lower().replace(" ", "_") for x in (text or "").split(";") if x.strip()]


def split_bp(text):
    """'140/90' -> (140, 90); '140' -> (140, None); '' -> (None, None). Raises ValueError otherwise."""
    text = (text or "").strip()
    if not text:
        return None, None
    m = re.fullmatch(r"(\d{2,3})\s*(?:/\s*(\d{2,3}))?", text)
    if not m:
        raise ValueError(f"cannot read BP '{text}'")
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def num(text, kind=float):
    text = (text or "").strip()
    return kind(text) if text else None


def ensure_goal_column(con):
    """Add patients.goal_override to an older database that does not have it yet."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(patients)")}
    if "goal_override" not in cols:
        con.execute("ALTER TABLE patients ADD COLUMN goal_override TEXT "
                    "CHECK (goal_override IS NULL OR goal_override = 'high_risk')")


def read_goal(row):
    value = (row.get("goal_override") or "").strip().lower()
    if value in ("", "none", "null"):
        return None
    if value != "high_risk":
        raise ValueError(f"goal_override must be empty or high_risk, not '{value}'")
    return "high_risk"


def import_csv(con, path):
    ensure_goal_column(con)
    added_p, added_e, bad = 0, 0, []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):  # line 1 is the header
            try:
                ref = row["patient_ref"].strip()
                if not ref:
                    raise ValueError("patient_ref is empty")
                sbp, dbp = split_bp(row.get("bp"))
                positives = split_list(row.get("positive_results"))
                goal = read_goal(row)
                key = f"import_ref:{hashlib.sha256(ref.encode()).hexdigest()[:16]}"
                pid = get_meta(con, key)
                if pid is None:
                    sex = (row.get("sex") or "").strip().lower() or None
                    if sex in ("m", "f"):
                        sex = {"m": "male", "f": "female"}[sex]
                    smoker = (row.get("smoker") or "").strip().lower() or None
                    cur = con.execute(
                        "INSERT INTO patients (sex, age, number_of_children, smoker, height_cm, weight_kg, village, "
                        "phone_hash, language, enrolled_on, condition_codes, goal_override) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                        (sex, num(row.get("age"), int), num(row.get("number_of_children"), int), smoker,
                         num(row.get("height_cm")), num(row.get("weight_kg")), row.get("village") or None,
                         row.get("language") or "sw", row["date"][:10], json.dumps(positives), goal),
                    )
                    pid = cur.lastrowid
                    set_meta(con, key, pid)
                    audit(con, "import", "patients", pid, user_id="import")
                    added_p += 1
                else:
                    pid = int(pid)
                    # New positive results add to the condition codes; the latest weight is kept.
                    old = json.loads(con.execute("SELECT condition_codes FROM patients WHERE id = ?", (pid,)).fetchone()[0] or "[]")
                    con.execute("UPDATE patients SET condition_codes = ? WHERE id = ?",
                                (json.dumps(sorted(set(old) | set(positives))), pid))
                    if num(row.get("weight_kg")):
                        con.execute("UPDATE patients SET weight_kg = ? WHERE id = ?", (num(row.get("weight_kg")), pid))
                    if goal:
                        con.execute("UPDATE patients SET goal_override = ? WHERE id = ?", (goal, pid))
                source = (row.get("source") or "clinic").strip().lower()
                if source not in ("clinic", "chw", "sms"):
                    raise ValueError(f"source must be clinic, chw or sms, not '{source}'")
                cur = con.execute(
                    "INSERT INTO encounters (patient_id, date, source, complaint_text, tests_performed, positive_results, "
                    "referral, doctor_recommendation_text, doctor_notes_text, chw_notes_text, bp_text, sbp, dbp, "
                    "blood_sugar, blood_sugar_unit, symptom_codes, lang) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)",
                    (pid, row["date"][:10], source, row.get("complaint") or None,
                     json.dumps(split_list(row.get("tests_performed"))), json.dumps(positives),
                     json.dumps(split_list(row.get("referral"))), row.get("doctor_recommendation") or None,
                     row.get("doctor_notes") or None, row.get("chw_notes") or None, (row.get("bp") or "").strip() or None,
                     sbp, dbp, num(row.get("blood_sugar")), row.get("blood_sugar_unit") or None, row.get("language") or None),
                )
                audit(con, "import", "encounters", cur.lastrowid, user_id="import")
                added_e += 1
            except (ValueError, KeyError) as e:
                bad.append({"line": i, "error": str(e)})
    con.commit()
    return {"patients_added": added_p, "encounters_added": added_e, "rows_not_read": bad}


def main():
    parser = argparse.ArgumentParser(description="Import existing records from a CSV.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--csv", required=True)
    args = parser.parse_args()
    con = connect(args.db)
    out = import_csv(con, args.csv)
    print(f"Patients added: {out['patients_added']}")
    print(f"Encounters added: {out['encounters_added']}")
    if out["rows_not_read"]:
        print("Rows not read:")
        for b in out["rows_not_read"]:
            print(f"  line {b['line']}: {b['error']}")


if __name__ == "__main__":
    main()
