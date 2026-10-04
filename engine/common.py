"""Small helpers every engine file uses: open the database, read the config, dates, JSON."""

import json
import os
import sqlite3
from datetime import date, datetime, timedelta

# The repo root, so relative paths in the config work from any folder.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def root_path(path):
    """Turn a path from the config into an absolute path (relative paths start at the repo root)."""
    if path is None:
        return None
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def connect(db_path):
    """Open the SQLite database. Rows behave like dicts: row['sbp']."""
    con = sqlite3.connect(root_path(db_path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def load_config(config_path):
    """Read config/guideline_htn.json."""
    with open(root_path(config_path), encoding="utf-8") as f:
        return json.load(f)


def loads(text, default=None):
    """Read JSON text from the database; return default when empty or broken."""
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def dumps(value):
    """Write a value as JSON text for the database."""
    return json.dumps(value, ensure_ascii=False)


def to_date(value):
    """Accept a date, a datetime, or 'YYYY-MM-DD...' text; return a date."""
    if value is None or value == "":
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def iso(d):
    return to_date(d).isoformat()


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def days_between(a, b):
    return (to_date(b) - to_date(a)).days


def add_days(d, n):
    return (to_date(d) + timedelta(days=n)).isoformat()


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))


def audit(con, action, table_name, row_id, user_id="chw"):
    """Write one row to the audit table: who changed what, and when."""
    con.execute(
        "INSERT INTO audit (user_id, action, table_name, row_id, at) VALUES (?, ?, ?, ?, ?)",
        (user_id, action, table_name, row_id, now_iso()),
    )


def bp_series(con, patient_id, as_of=None):
    """All blood pressure readings for one patient, oldest first, up to as_of."""
    as_of = iso(as_of)
    rows = con.execute(
        "SELECT id, date, sbp, dbp, source FROM encounters "
        "WHERE patient_id = ? AND sbp IS NOT NULL AND date <= ? ORDER BY date, id",
        (patient_id, as_of + "T99"),
    ).fetchall()
    return [dict(r) for r in rows]


def last_contact_date(con, patient_id, as_of=None):
    """The latest date we heard from or saw the patient: an encounter or an incoming SMS."""
    as_of = iso(as_of)
    row = con.execute(
        "SELECT MAX(d) AS d FROM ("
        " SELECT MAX(date) AS d FROM encounters WHERE patient_id = ? AND date <= ?"
        " UNION ALL"
        " SELECT MAX(substr(received_at, 1, 10)) FROM messages"
        "   WHERE patient_id = ? AND direction = 'in' AND substr(received_at, 1, 10) <= ?)",
        (patient_id, as_of + "T99", patient_id, as_of),
    ).fetchone()
    return row["d"][:10] if row and row["d"] else None


def action_rank(cfg, action):
    """How urgent an action is, by its place in config.actions (not_sure ranks lowest)."""
    order = [a for a in cfg["actions"] if a != "not_sure"]
    return order.index(action) if action in order else -1
