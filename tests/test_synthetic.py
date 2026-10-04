"""Step 2 and 2b: the synthetic database and the labeled sets."""

import os
import subprocess
import sys

from tests.conftest import ROOT, patients_by


def test_sixty_patients_and_groups(con, cfg):
    assert con.execute("SELECT COUNT(*) FROM patients WHERE status = 'active'").fetchone()[0] == 60
    assert con.execute("SELECT COUNT(*) FROM patients WHERE status = 'former'").fetchone()[0] == 30
    assert con.execute("SELECT value FROM meta WHERE key = 'synthetic'").fetchone()[0] == "true"
    for group in ("silent", "missed_appointments", "missed_meds", "smoker_high_bmi", "emergency_symptom"):
        assert patients_by(con, group=group), group
    assert patients_by(con, trajectory="drifting") and patients_by(con, trajectory="stable")
    # 8 events among active patients, 18 among former ones: enough for the peer-trajectory model.
    active_events = con.execute("SELECT COUNT(*) FROM events e JOIN patients p ON p.id = e.patient_id "
                                "WHERE p.status = 'active'").fetchone()[0]
    assert active_events == 8
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= cfg["trajectory"]["min_events"]


def test_former_patients_end_before_today(con):
    last = con.execute("SELECT MAX(e.date) FROM encounters e JOIN patients p ON p.id = e.patient_id "
                       "WHERE p.status = 'former'").fetchone()[0]
    assert last < "2026-08-05"  # at least 60 days before the end date
    assert not con.execute("SELECT 1 FROM followups f JOIN patients p ON p.id = f.patient_id "
                           "WHERE p.status = 'former' AND f.done_on IS NULL").fetchone()


def test_silent_patients_never_text(con):
    for pid in patients_by(con, group="silent"):
        n = con.execute("SELECT COUNT(*) FROM messages WHERE patient_id = ? AND direction = 'in'", (pid,)).fetchone()[0]
        assert n == 0


def test_bp_text_matches_split_numbers(con):
    for r in con.execute("SELECT bp_text, sbp, dbp FROM encounters WHERE sbp IS NOT NULL LIMIT 200").fetchall():
        assert r["bp_text"] == f"{r['sbp']}/{r['dbp']}"


def test_make_labels(db, tmp_path):
    out = tmp_path / "labels"
    subprocess.run([sys.executable, os.path.join(ROOT, "data", "make_labels.py"), "--db", db, "--out", str(out)],
                   check=True, capture_output=True)
    import pandas as pd
    sms = pd.read_csv(out / "sms_labeled.csv")
    assert len(sms) == 300 and sms["messy"].sum() == 60
    dec = pd.read_csv(out / "decisions_labeled.csv")
    assert {"state", "gold_action", "deteriorates_30d"} <= set(dec.columns)
    phys = pd.read_csv(out / "physician_sample.csv")
    assert len(phys) == 50 and "physician_action" in phys.columns
