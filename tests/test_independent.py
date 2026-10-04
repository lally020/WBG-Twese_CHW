"""The independent test set: built separately (other seed, other phrasings, no hand-built patient), and the
evaluation trains on the development set and scores on the held-out set."""

import os
import sqlite3
import subprocess
import sys

import pytest

from tests.conftest import CONFIG, END, ROOT


@pytest.fixture(scope="module")
def test_db(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("held_out") / "test.db")
    subprocess.run([sys.executable, os.path.join(ROOT, "data", "make_synthetic.py"), "--db", db, "--seed", "2",
                    "--text-variant", "test", "--end-date", END], check=True, capture_output=True)
    return db


def _phrasings(db):
    con = sqlite3.connect(db)
    return {r[0].split(": ", 1)[1] for r in con.execute(
        "SELECT text_modifier_reason FROM synthetic_truth WHERE text_modifier != 'none'")}


def test_held_out_set_shares_no_sentence_and_no_demo_patient(base_db, test_db):
    dev, held = _phrasings(base_db), _phrasings(test_db)
    assert dev and held and not dev & held
    a = sqlite3.connect(base_db).execute("SELECT age, village FROM patients WHERE id = 1").fetchone()
    b = sqlite3.connect(test_db).execute("SELECT age, village FROM patients WHERE id = 1").fetchone()
    assert a == (58, "Kirundo") and b != a


def test_trajectory_external(base_db, test_db):
    from engine import trajectory
    from engine.common import connect, load_config
    out = trajectory.evaluate_external(connect(base_db), connect(test_db), load_config(CONFIG), END)
    assert out["auc_leave_one_patient_out"] is not None and out["windows"] > 100


def test_retrain_independent(base_db, test_db, tmp_path):
    from engine import evaluate
    for db, name in ((base_db, "dev"), (test_db, "held")):
        subprocess.run([sys.executable, os.path.join(ROOT, "data", "make_labels.py"), "--db", db,
                        "--out", str(tmp_path / name), "--no-physician-sample"], check=True, capture_output=True)
    assert not (tmp_path / "held" / "physician_sample.csv").exists()
    out = evaluate.eval_retrain_independent(str(tmp_path / "dev"), str(tmp_path / "held"))
    assert out["decider"]["n_test"] > 0 and 0 <= out["decider"]["accuracy"] <= 1
    assert out["deterioration"]["auc_heldout_patients"] is not None
