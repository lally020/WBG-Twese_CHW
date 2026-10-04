"""Create the SQLite database from the schema file.

Usage:
    python data/make_db.py --db data/chw.db --schema data/schema.sql

Safe to run twice: tables are only created if they do not exist yet.
Add --reset to delete the database file first and start empty.
"""

import argparse
import os
import sqlite3

SCHEMA_VERSION = "1"


def make_db(db_path, schema_path, reset=False):
    """Create the database and return the list of table names."""
    if reset and os.path.exists(db_path):
        os.remove(db_path)

    # Make the folder for the database if it is missing.
    folder = os.path.dirname(db_path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(schema_path, encoding="utf-8") as f:
        schema_sql = f.read()

    con = sqlite3.connect(db_path)
    try:
        con.executescript(schema_sql)
        # Record the schema version so other scripts can check it.
        con.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        con.commit()
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def main():
    parser = argparse.ArgumentParser(description="Create the TWESE CHW AI database.")
    parser.add_argument("--db", required=True, help="path to the SQLite file, for example data/chw.db")
    parser.add_argument("--schema", required=True, help="path to schema.sql")
    parser.add_argument("--reset", action="store_true", help="delete the database first")
    args = parser.parse_args()

    tables = make_db(args.db, args.schema, reset=args.reset)
    print(f"Database ready: {args.db}")
    print(f"{len(tables)} tables:")
    for name in tables:
        print(f"  {name}")


if __name__ == "__main__":
    main()
