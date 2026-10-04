"""Steps 11 and 11b: metrics, the monthly review, and peer trajectories."""

import os
import subprocess
import sys

from engine import metrics, retrain, trajectory
from tests.conftest import END, ROOT, patients_by


def test_metrics_month(con, cfg):
    m = metrics.metrics(con, cfg, "2026-09")
    assert m["suggestions"]["confirmed"] > 0
    assert 0 <= m["suggestions"]["override_rate"] <= 1
    assert m["hours_per_week"] > 0
    assert m["bp"]["patients_with_reading"] > 0


def test_review_creates_new_version(db, con, cfg, tmp_path):
    labels = tmp_path / "labels"
    subprocess.run([sys.executable, os.path.join(ROOT, "data", "make_labels.py"), "--db", db, "--out", str(labels)],
                   check=True, capture_output=True)
    cfg["paths"]["models_dir"] = str(tmp_path / "models")
    out = retrain.run_review(con, cfg, str(labels))
    assert out["model_version"] == "lr-v1"
    assert out["decider"]["accuracy"] > 0.5
    assert out["learned_from"]["overrides"] > 0
    assert retrain.run_review(con, cfg, str(labels))["model_version"] == "lr-v2"


def test_trajectory_off_without_enough_events(con, cfg):
    cfg["trajectory"]["min_events"] = 100
    res = trajectory.score_all(con, cfg, END)
    assert all(r["flag"] == "no" and r["share"] is None for r in res.values())
    assert "not enough local events" in next(iter(res.values()))["reason_text"].lower()


def test_trajectory_flags_deteriorating_not_stable(con, cfg):
    res = trajectory.score_all(con, cfg, END)  # the config minimum, with the former-patient history
    assert all(p in {r[0] for r in con.execute("SELECT id FROM patients WHERE status = 'active'")} for p in res)
    det = [p for p in patients_by(con, d30="yes") if p in res]
    stable = [p for p in patients_by(con, "stable") if p in res]
    assert sum(res[p]["flag"] == "yes" for p in det) >= len(det) // 2
    assert sum(res[p]["flag"] == "yes" for p in stable) <= 2
    flagged = next(res[p] for p in det if res[p]["flag"] == "yes")
    assert len(flagged["peers"]) == cfg["trajectory"]["show_peers"]
    from engine import risk
    assert risk.score_patient(con, cfg, 1, END)["tier"] == "medium"  # demo patient: medium before the SMS, either way


def test_trajectory_peers_anonymised(con, cfg):
    pid = patients_by(con, d30="yes")[0]
    out = trajectory.for_patient(con, cfg, pid, END)
    assert all("patient_id" not in p and p["peer"].startswith("peer-") for p in out["peers"])


def test_trajectory_leave_one_out(con, cfg):
    ev = trajectory.evaluate(con, cfg, END)
    assert ev["auc_leave_one_patient_out"] is not None and ev["windows"] > 100


def test_trajectory_score_one_matches_score_all(con, cfg):
    every = trajectory.score_all(con, cfg, END)
    pid = patients_by(con, d30="yes")[0]
    one = trajectory.score_one(con, cfg, pid, END, write=False)
    assert one["share"] == every[pid]["share"] and one["flag"] == every[pid]["flag"]
