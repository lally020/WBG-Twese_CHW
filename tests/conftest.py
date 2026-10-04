"""Shared test setup: one synthetic database (fixed seed and end date), copied fresh for each test."""

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "guideline_htn.json")
END = "2026-10-03"


@pytest.fixture(autouse=True)
def no_julia(request, monkeypatch):
    """Tests run without Julia-1 so the results do not depend on it; tests marked 'julia' use the real model."""
    if "julia" in request.keywords:
        return
    from engine import models
    monkeypatch.setitem(models._julia, "engine", None)
    monkeypatch.setitem(models._julia, "tried", True)
    monkeypatch.setitem(models._julia, "error", "disabled in tests")


@pytest.fixture(scope="session")
def base_db(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("db") / "base.db")
    subprocess.run([sys.executable, os.path.join(ROOT, "data", "make_db.py"), "--db", db,
                    "--schema", os.path.join(ROOT, "data", "schema.sql")], check=True, capture_output=True)
    subprocess.run([sys.executable, os.path.join(ROOT, "data", "make_synthetic.py"), "--db", db, "--patients", "60",
                    "--months", "6", "--seed", "1", "--end-date", END], check=True, capture_output=True)
    return db


@pytest.fixture
def db(base_db, tmp_path):
    path = str(tmp_path / "test.db")
    shutil.copy(base_db, path)
    return path


@pytest.fixture
def con(db):
    from engine.common import connect
    c = connect(db)
    yield c
    c.close()


@pytest.fixture
def cfg():
    from engine.common import load_config
    return load_config(CONFIG)


def patients_by(con, trajectory=None, group=None, d30=None):
    """Synthetic patient ids by hidden trajectory or group (from synthetic_truth)."""
    sql, params = "SELECT patient_id, groups_json FROM synthetic_truth WHERE 1=1", []
    if trajectory:
        sql += " AND trajectory = ?"
        params.append(trajectory)
    if d30:
        sql += " AND deteriorates_within_30d = ?"
        params.append(d30)
    rows = con.execute(sql, params).fetchall()
    return [r["patient_id"] for r in rows if group is None or group in (r["groups_json"] or "")]
