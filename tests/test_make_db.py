"""Step 1: the database is created with every table from PLAN.md section 4."""

import os
import sqlite3

from data.make_db import make_db

HERE = os.path.dirname(__file__)
SCHEMA = os.path.join(HERE, "..", "data", "schema.sql")

EXPECTED = {
    "patients", "encounters", "medications", "messages", "flags", "risk_scores",
    "plans", "decisions", "followups", "quiz_results", "events", "trajectory_scores",
    "feedback", "time_log", "llm_log", "audit", "meta", "synthetic_truth",
}


def test_all_tables_created(tmp_path):
    db = str(tmp_path / "test.db")
    tables = set(make_db(db, SCHEMA))
    assert EXPECTED <= tables


def test_runs_twice_without_error(tmp_path):
    db = str(tmp_path / "test.db")
    make_db(db, SCHEMA)
    make_db(db, SCHEMA)
    con = sqlite3.connect(db)
    version = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    con.close()
    assert version is not None


def test_encounters_keep_existing_record_fields(tmp_path):
    db = str(tmp_path / "test.db")
    make_db(db, SCHEMA)
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(encounters)")}
    con.close()
    for field in ["complaint_text", "tests_performed", "positive_results", "referral",
                  "doctor_recommendation_text", "doctor_notes_text", "chw_notes_text",
                  "bp_text", "sbp", "dbp", "blood_sugar", "blood_sugar_unit"]:
        assert field in cols
