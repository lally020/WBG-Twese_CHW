"""Step 8: a patient with a symptom flag ranks above a stable patient; a silent patient's score rises over time."""

from engine import risk, signals
from tests.conftest import END, patients_by


def test_symptom_patient_above_stable(con, cfg):
    sym = next(p for p in patients_by(con, d30="yes")
               if any(f["type"] == "symptom" for f in signals.compute(con, cfg, p, END)["flags"]))
    stable = patients_by(con, "stable")[0]
    a = risk.score_patient(con, cfg, sym, END)
    b = risk.score_patient(con, cfg, stable, END)
    assert a["score"] > b["score"]
    assert a["components"]["symptom_flag"]["points"] >= cfg["risk"]["points"]["symptom_flag"]


def test_silent_patient_rises(con, cfg):
    pid = patients_by(con, group="silent")[0]
    now = risk.score_patient(con, cfg, pid, END)["score"]
    later = risk.score_patient(con, cfg, pid, "2026-12-15")["score"]
    assert later > now


def test_tiers_and_storage(con, cfg):
    scores = risk.risk_all(con, cfg, END)
    assert len(scores) == 60  # active patients only
    assert scores[0]["tier"] == "high"
    assert con.execute("SELECT COUNT(*) FROM risk_scores WHERE date = ?", (END,)).fetchone()[0] == 60
    # The WHO chart is loaded: every patient aged 40 or more with a reading has a 10-year risk.
    for s in scores:
        age = con.execute("SELECT age FROM patients WHERE id = ?", (s["patient_id"],)).fetchone()[0]
        if age >= 40:
            assert s["components"]["who_base"]["percent_10y"] is not None, s["patient_id"]


def test_who_lookup_matches_chart(con, cfg):
    from engine import who_risk
    # Page 4, woman, non-smoker, 55-59, SBP 140-159, BMI 25-29: 6%. Man, smoker, 70-74, SBP 180+, BMI 35+: 38%.
    woman = {"age": 57, "sex": "female", "smoker": "no", "height_cm": 160, "weight_kg": 70}
    man = {"age": 72, "sex": "male", "smoker": "yes", "height_cm": 170, "weight_kg": 110}
    row = lambda d: type("R", (), {"__getitem__": lambda self, k: d[k], "keys": lambda self: d.keys()})()  # noqa: E731
    assert who_risk.lookup(cfg, row(woman), 145)["percent"] == 6
    assert who_risk.lookup(cfg, row(man), 185)["percent"] == 38


def test_who_age_rules(con, cfg):
    from engine import who_risk
    row = lambda d: type("R", (), {"__getitem__": lambda self, k: d[k], "keys": lambda self: d.keys()})()  # noqa: E731
    old = who_risk.lookup(cfg, row({"age": 80, "sex": "male", "smoker": "yes", "height_cm": 170, "weight_kg": 110}), 185)
    assert old["percent"] == 38 and "above the chart" in old["age_note"]
    young = who_risk.lookup(cfg, row({"age": 35, "sex": "male", "smoker": "yes", "height_cm": 170, "weight_kg": 110}), 185)
    assert young["percent"] is None and "below the chart" in young["reason"]
