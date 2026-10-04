"""Step 2. Fill the database with synthetic hypertension patients. No real data.

Usage:
    python data/make_synthetic.py --db data/chw.db --patients 60 --months 6 --seed 1

What it makes (PLAN.md step 2):
- patients across 6 villages, with every existing record field;
- clinic encounters every 2 to 3 months, CHW encounters every 2 to 4 weeks with notes,
  Swahili/French/English SMS reports from content/i18n/*_examples.json;
- groups: stable, drifting, deteriorating now, past events (stroke or admission), missed medication,
  smokers with high BMI, emergency symptoms, missed clinic appointments, silent (never send SMS);
- follow-ups and appointments (some missed), time_log rows with real minutes per village,
  a few past quiz answers and past decisions;
- patient 1 is the demo patient (age 58, Kirundo, Swahili), tuned for the step 13 scenario;
- the hidden trajectory of each patient in synthetic_truth (for evaluation only);
- synthetic = true in the meta table.
The script empties every table first, so running it twice gives the same database.
"""

import argparse
import hashlib
import json
import os
import random
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import connect, dumps, load_config, loads, root_path, set_meta

# Village: (travel minutes from the CHW's base, usual visit minutes).
VILLAGES = {
    "Kirundo": (15, 25),
    "Ngozi": (25, 30),
    "Muyinga": (40, 30),
    "Gitega": (20, 25),
    "Cibitoke": (55, 35),
    "Rutana": (35, 30),
}

TABLES = ["error_reviews", "llm_log", "audit", "feedback", "time_log", "trajectory_scores", "events", "quiz_results",
          "followups", "decisions", "plans", "risk_scores", "flags", "messages", "medications",
          "encounters", "synthetic_truth", "patients", "meta"]

DOSES = {
    "amlodipine": ["5 mg once daily", "10 mg once daily"],
    "nifedipine": ["20 mg twice daily", "30 mg once daily"],
    "hydrochlorothiazide": ["12.5 mg once daily", "25 mg once daily"],
    "enalapril": ["5 mg twice daily", "10 mg once daily"],
    "lisinopril": ["10 mg once daily", "20 mg once daily"],
    "losartan": ["50 mg once daily"],
    "atenolol": ["50 mg once daily"],
    "methyldopa": ["250 mg twice daily"],
    "furosemide": ["20 mg once daily"],
    "aspirin": ["75 mg once daily"],
}

COMPLAINTS = {
    "en": ["Routine check", "Came for blood pressure review", "Mild headache sometimes", "Needs refill of medicine"],
    "fr": ["Contrôle de routine", "Renouvellement du traitement", "Maux de tête légers parfois", "Suivi de la tension"],
    "sw": ["Ukaguzi wa kawaida", "Nimekuja kupima presha", "Kichwa kinauma kidogo mara moja moja", "Dawa zimeisha"],
}
DOCTOR_NOTES = {
    "en": ["BP not at goal, continue treatment and lifestyle advice.", "Stable. Continue.",
           "Discussed salt reduction.", "Adherence counselling given."],
    "fr": ["TA non contrôlée, poursuivre le traitement.", "Stable. Continuer.",
           "Conseils sur la réduction du sel.", "Éducation sur l'observance."],
}
CHW_NOTES = {
    "ok": ["Patient well, taking medicine.", "Mgonjwa yuko sawa, anakunywa dawa.",
           "Patient bien, prend ses médicaments.", "Home visit, no complaints."],
    "missed": ["Patient ran out of tablets last week.", "Amesahau dawa siku kadhaa.",
               "N'a pas pris ses comprimés depuis 3 jours.", "Takes medicine only when feeling unwell."],
    "symptom": ["Patient reports headache and dizziness.", "Analalamika kizunguzungu.",
                "Se plaint de maux de tête."],
}
EMERGENCY_NOTES = {
    "face_droop": "Family says one side of the face is drooping since this morning.",
    "arm_weakness": "Patient cannot lift left arm, started today.",
    "speech_difficulty": "Speech slurred since yesterday evening, family worried.",
}
OVERRIDE_REASONS = ["patient travelling", "already seen at clinic", "family will bring patient",
                    "nurse advised", "patient feels well, recheck first"]


def load_examples(content_dir, lang):
    path = os.path.join(content_dir, "i18n", f"{lang}_examples.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)["examples"]


def load_quiz(content_dir):
    path = os.path.join(content_dir, "quiz", "patient_htn.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)["questions"]


class Builder:
    """Holds the random generator, the database connection, and the dates."""

    def __init__(self, con, cfg, rng, end, months, examples, quiz, chw_id, text_variant="dev"):
        self.text_variant = text_variant
        self.con, self.cfg, self.rng = con, cfg, rng
        self.end = end
        self.start = end - timedelta(days=months * 30)
        self.examples = examples
        self.quiz = quiz
        self.chw_id = chw_id
        self.summary = {}

    # ----- small helpers -----

    def d(self, day):
        """Day number since start -> 'YYYY-MM-DD'."""
        return (self.start + timedelta(days=day)).isoformat()

    def insert(self, table, **row):
        cols = ", ".join(f'"{c}"' for c in row)
        marks = ", ".join("?" for _ in row)
        cur = self.con.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
        return cur.lastrowid

    def count(self, group):
        self.summary[group] = self.summary.get(group, 0) + 1

    # ----- patients -----

    def make_patient(self, pid, p):
        rng = self.rng
        bmi_target = p.get("bmi", rng.uniform(20, 29))
        height = round(rng.uniform(152, 182) if p["sex"] == "male" else rng.uniform(148, 172), 0)
        weight = round(bmi_target * (height / 100) ** 2, 1)
        self.insert(
            "patients", id=pid, sex=p["sex"], age=p["age"], number_of_children=rng.randint(0, 8),
            smoker=p["smoker"], height_cm=height, weight_kg=weight, village=p["village"],
            phone_hash=hashlib.sha256(f"+257 7{pid:07d}".encode()).hexdigest()[:16],
            language=p["language"], enrolled_on=(self.start - timedelta(days=rng.randint(30, 400))).isoformat(),
            condition_codes=dumps(["hypertension"]), status=p.get("status", "active"), chw_id=p.get("chw_id", 1),
        )

    def bp_at(self, p, day):
        """The synthetic blood pressure of patient p on a given day (before noise)."""
        sbp = p["base_sbp"]
        kind = p["trajectory"]
        if kind == "drifting":
            sbp += p["rate"] * (day - p.get("day0", 0)) / 7
        elif kind == "deteriorating_now":
            ramp_start = p["days"] - 35
            if day > ramp_start:
                sbp += p["ramp"] * (day - ramp_start) / 35
        elif kind == "event":
            ramp_start = p["event_day"] - 42
            if ramp_start < day <= p["event_day"]:
                sbp += p["ramp"] * (day - ramp_start) / 42
            elif day > p["event_day"]:
                sbp -= 4  # treated after the event
        return sbp

    def reading(self, p, day, meds):
        rng = self.rng
        sbp = self.bp_at(p, day) + rng.gauss(0, p.get("noise", 4.5))
        if meds == "no":
            sbp += 6
        elif meds == "partial":
            sbp += 3
        sbp = int(round(sbp))
        dbp = int(round(p["base_dbp"] + (sbp - p["base_sbp"]) * 0.5 + rng.gauss(0, 3)))
        return sbp, dbp

    def meds_for(self, p):
        if "missed_meds" in p["groups"]:
            return self.rng.choice(["no", "partial", "partial", "yes"])
        return self.rng.choice(["yes"] * 9 + ["partial"])

    # ----- encounters -----

    def clinic(self, pid, p, day):
        rng = self.rng
        meds = self.meds_for(p)
        sbp, dbp = self.reading(p, day, meds)
        lang = "fr" if p["language"] == "fr" else rng.choice(["en", "fr"])
        drugs = p["drugs"]
        rec = ". ".join(f"{n.capitalize()} {DOSES[n][0] if n in DOSES else ''}".strip() for n in drugs)
        rec += ". Reduce salt." if lang == "en" else ". Réduire le sel."
        referral = ["clinic_review"] if sbp >= 165 and rng.random() < 0.5 else []
        sugar = round(rng.uniform(4.5, 8.5), 1)
        eid = self.insert(
            "encounters", patient_id=pid, date=self.d(day), source="clinic",
            complaint_text=rng.choice(COMPLAINTS["sw" if p["language"] in ("sw", "rn") else lang]),
            tests_performed=dumps(["bp", "blood_sugar", "weight"]),
            positive_results=dumps(["hypertension"]), referral=dumps(referral),
            doctor_recommendation_text=rec, doctor_notes_text=rng.choice(DOCTOR_NOTES[lang]),
            bp_text=f"{sbp}/{dbp}", sbp=sbp, dbp=dbp, blood_sugar=sugar, blood_sugar_unit="mmol/L",
            meds_taken=meds, symptom_codes=dumps([]), lang=lang,
        )
        for n in drugs:
            self.insert("medications", patient_id=pid, encounter_id=eid, name=n,
                        dose_text=DOSES.get(n, [""])[0], source_text=rec)
        return eid

    def chw_visit(self, pid, p, day, symptoms=None, note=None):
        rng = self.rng
        meds = self.meds_for(p)
        sbp, dbp = self.reading(p, day, meds)
        if note is None:
            note = rng.choice(CHW_NOTES["missed" if meds in ("no", "partial") and rng.random() < 0.6 else "ok"])
        eid = self.insert(
            "encounters", patient_id=pid, date=self.d(day), source="chw",
            chw_notes_text=note, bp_text=f"{sbp}/{dbp}", sbp=sbp, dbp=dbp, meds_taken=meds,
            symptom_codes=dumps(symptoms or []), lang="en",
        )
        self.log_visit(pid, p, day)
        return eid

    def log_visit(self, pid, p, day):
        rng = self.rng
        travel, visit = VILLAGES[p["village"]]
        hour = rng.randint(8, 14)
        start = self.start + timedelta(days=day)
        t0 = f"{start.isoformat()}T{hour:02d}:00:00"
        tr = max(5, int(rng.gauss(travel, travel * 0.2)))
        vi = max(10, int(rng.gauss(visit, 6)))
        mid = f"{start.isoformat()}T{hour + tr // 60:02d}:{tr % 60:02d}:00"
        end_min = tr + vi
        t1 = f"{start.isoformat()}T{hour + end_min // 60:02d}:{end_min % 60:02d}:00"
        self.insert("time_log", chw_id=p.get("chw_id", self.chw_id), activity="travel", patient_id=pid, village=p["village"],
                    start=t0, end=mid)
        self.insert("time_log", chw_id=p.get("chw_id", self.chw_id), activity="visit", patient_id=pid, village=p["village"],
                    start=mid, end=t1)

    def pick_example(self, lang, intent, meds=None, symptoms=False):
        pool = [e for e in self.examples.get(lang, []) if e["intent"] == intent]
        if intent == "bp_report":
            want = [e for e in pool if (bool(e["symptoms"]) == symptoms)
                    and (e["meds_taken"] in (meds, None))]
            pool = want or pool
        return self.rng.choice(pool) if pool else None

    def sms_report(self, pid, p, day, symptoms=False):
        """An incoming SMS with a BP report, already confirmed into an encounter."""
        rng = self.rng
        lang = {"sw": "sw", "rn": "sw", "fr": "fr", "en": "en"}[p["language"]]
        meds = self.meds_for(p)
        ex = self.pick_example(lang, "bp_report", meds, symptoms)
        if ex is None:
            return None
        sbp, dbp = self.reading(p, day, ex["meds_taken"] or meds)
        text = ex["template"].format(sbp=sbp, dbp=dbp)
        when = f"{self.d(day)}T{rng.randint(6, 20):02d}:{rng.randint(0, 59):02d}:00"
        fields = {"sbp": sbp, "dbp": dbp, "meds_taken": ex["meds_taken"], "symptoms": ex["symptoms"]}
        eid = self.insert(
            "encounters", patient_id=pid, date=self.d(day), source="sms", complaint_text=text,
            bp_text=f"{sbp}/{dbp}", sbp=sbp, dbp=dbp, meds_taken=ex["meds_taken"],
            symptom_codes=dumps(ex["symptoms"]), lang=lang,
        )
        parsed = {"intent": "bp_report", "fields": fields, "valid": True, "confirmed": True,
                  "backend": "synthetic"}
        self.insert("messages", patient_id=pid, direction="in", kind="report", lang=lang, text=text,
                    received_at=when, parsed_json=dumps(parsed), encounter_id=eid, status=None)
        sms_end = (datetime.fromisoformat(when) + timedelta(minutes=self.cfg["capacity"]["sms_minutes"])).isoformat()
        self.insert("time_log", chw_id=p.get("chw_id", self.chw_id), activity="sms", patient_id=pid, village=p["village"],
                    start=when, end=sms_end)
        return eid

    def sms_other(self, pid, p, day):
        """An incoming SMS that is not a report (question, greeting), already handled."""
        lang = {"sw": "sw", "rn": "sw", "fr": "fr", "en": "en"}[p["language"]]
        ex = self.pick_example(lang, self.rng.choice(["meds_question", "other"]))
        if ex is None:
            return
        when = f"{self.d(day)}T{self.rng.randint(6, 20):02d}:{self.rng.randint(0, 59):02d}:00"
        parsed = {"intent": ex["intent"], "fields": {}, "valid": True, "confirmed": True, "backend": "synthetic"}
        self.insert("messages", patient_id=pid, direction="in", kind="other", lang=lang, text=ex["template"],
                    received_at=when, parsed_json=dumps(parsed), status=None)

    # ----- one patient's six months -----

    def build_patient(self, pid, p):
        rng = self.rng
        self.make_patient(pid, p)
        last_day = p["days"] - 1
        stop = p.get("stop_day", last_day)  # silent patients stop being seen early

        # Clinic appointments every 60 to 90 days.
        day = rng.randint(0, 20)
        while True:
            missed = "missed_appointments" in p["groups"] and rng.random() < 0.5 and day > 30
            if day > last_day:
                # A future appointment: due, not done.
                self.insert("followups", patient_id=pid, due_date=self.d(day), kind="clinic_appointment",
                            status="due")
                break
            if missed:
                self.insert("followups", patient_id=pid, due_date=self.d(day), kind="clinic_appointment",
                            status="missed")
            else:
                self.clinic(pid, p, day)
                self.insert("followups", patient_id=pid, due_date=self.d(day), kind="clinic_appointment",
                            status="done", done_on=self.d(day))
            day += rng.randint(60, 90)

        # CHW visits every 14 to 28 days (35 to 56 for silent patients).
        gap = (35, 56) if "silent" in p["groups"] else (14, 28)
        day = rng.randint(3, 14)
        last_visit = None
        while day <= stop:
            self.chw_visit(pid, p, day)
            last_visit = day
            day += rng.randint(*gap)
        if last_visit is not None:
            nxt = last_visit + rng.randint(*gap)
            due = self.d(nxt)
            status = "missed" if nxt < last_day and "silent" in p["groups"] else "due"
            self.insert("followups", patient_id=pid, due_date=due, kind="chw_visit", status=status)

        # SMS reports every 7 to 21 days, unless silent.
        if "silent" not in p["groups"]:
            day = rng.randint(5, 15)
            while day <= stop:
                self.sms_report(pid, p, day)
                if rng.random() < 0.15:
                    self.sms_other(pid, p, min(day + 1, last_day))
                day += rng.randint(7, 21)

    # ----- groups -----

    def plan_patients(self, n, months):
        """Decide each patient's hidden trajectory and groups."""
        rng = self.rng
        days = months * 30
        # Patient 1 is the hand-built demo patient, except in the held-out test set, where every patient is random.
        ids = list(range(2 if self.text_variant == "dev" else 1, n + 1))
        rng.shuffle(ids)
        n_event = max(1, round(n * 8 / 60))
        n_det = max(1, round(n * 6 / 60))
        n_drift = max(1, round(n * 15 / 60))
        plan = {}
        cursor = 0

        def take(k):
            nonlocal cursor
            chunk = ids[cursor:cursor + k]
            cursor += k
            return chunk

        for pid in take(n_event):
            plan[pid] = {"trajectory": "event", "event_day": rng.randint(60, days - 25), "ramp": rng.uniform(28, 42)}
        for pid in take(n_det):
            plan[pid] = {"trajectory": "deteriorating_now", "ramp": rng.uniform(25, 40)}
        for pid in take(n_drift):
            plan[pid] = {"trajectory": "drifting", "rate": rng.uniform(0.45, 0.9)}
        # Borderline patients (PLAN.md 4 October focus): just under the goal, some crossing it now and then.
        rest = ids[cursor:]
        n_border = max(1, round(n * 10 / 60)) if n >= 12 else 0
        for pid in rest[:n_border]:
            plan[pid] = {"trajectory": "borderline"}
        for pid in rest[n_border:]:
            plan[pid] = {"trajectory": "stable"}

        # More villages for a large clinic: about 600 patients per village, travel and visit times near
        # those of the six base villages.
        base = list(VILLAGES)
        for k in range(len(base), max(len(base), -(-n // 600))):
            name = f"{base[k % len(base)]} {k // len(base) + 1}"
            travel, visit = VILLAGES[base[k % len(base)]]
            VILLAGES[name] = (max(5, travel + rng.randint(-8, 12)), max(15, visit + rng.randint(-5, 5)))
        villages = list(VILLAGES)
        for pid, p in plan.items():
            p["days"] = days
            p["groups"] = []
            p["sex"] = rng.choice(["male", "female"])
            p["age"] = rng.randint(40, 78)
            p["smoker"] = "yes" if rng.random() < 0.2 else "no"
            p["village"] = rng.choice(villages)
            p["language"] = rng.choices(["sw", "rn", "fr", "en"], weights=[50, 25, 20, 5])[0]
            # Stable means controlled (around 126); borderline sits just under 140; the others start higher.
            g = rng.gauss(0, 1)
            if p["trajectory"] == "stable":
                p["base_sbp"] = max(112, min(134, 126 + 5 * g))
            elif p["trajectory"] == "borderline":
                p["base_sbp"], p["noise"] = max(131, min(138, 135 + 2.5 * g)), 4.0
            else:
                p["base_sbp"] = max(124, min(150, 137 + 6 * g))
            p["base_dbp"] = p["base_sbp"] * 0.63 + rng.gauss(0, 3)
            p["drugs"] = rng.sample(["amlodipine", "hydrochlorothiazide", "enalapril", "nifedipine",
                                     "lisinopril", "losartan", "atenolol"], rng.choice([1, 1, 2]))

        # Tags across trajectories.
        stable_or_drift = [pid for pid, p in plan.items() if p["trajectory"] in ("stable", "drifting")]
        rng.shuffle(stable_or_drift)
        others = list(plan)
        rng.shuffle(others)
        for pid in others[:max(1, round(n * 10 / 60))]:
            plan[pid]["groups"].append("missed_meds")
        for pid in others[-max(1, round(n * 8 / 60)):]:
            plan[pid]["groups"].append("smoker_high_bmi")
            plan[pid]["smoker"] = "yes"
            plan[pid]["bmi"] = rng.uniform(30, 36)
        for pid in stable_or_drift[:max(1, round(n * 10 / 60))]:
            plan[pid]["groups"].append("silent")
            plan[pid]["stop_day"] = days - rng.randint(35, 70)
        for pid in stable_or_drift[-max(1, round(n * 10 / 60)):]:
            plan[pid]["groups"].append("missed_appointments")
        # Emergency symptoms: one deteriorating-now patient and one drifting patient.
        det = [pid for pid, p in plan.items() if p["trajectory"] == "deteriorating_now"]
        drift = [pid for pid, p in plan.items() if p["trajectory"] == "drifting" and "silent" not in p["groups"]]
        if det:
            plan[det[0]]["groups"].append("emergency_symptom")
            plan[det[0]]["emergency_code"] = "arm_weakness"
        if drift:
            plan[drift[0]]["groups"].append("emergency_symptom")
            plan[drift[0]]["emergency_code"] = "speech_difficulty"
        # Low danger-sign understanding for a few talkative patients.
        talk = [pid for pid, p in plan.items() if "silent" not in p["groups"]]
        for pid in talk[:max(1, round(n * 5 / 60))]:
            plan[pid]["groups"].append("low_understanding")
        return plan

    # ----- demo patient -----

    def build_demo(self, days):
        """Patient 1: 58, Kirundo, Swahili. Readings drift up but stay just under the trend rule,
        so the demo SMS of 150/95 tips the trend flag (PLAN.md step 13)."""
        p = {"sex": "female", "age": 58, "smoker": "no", "village": "Kirundo", "language": "sw",
             "bmi": 23.5, "base_sbp": 134, "base_dbp": 86, "trajectory": "drifting", "groups": ["demo"],
             "days": days, "drugs": ["amlodipine"]}
        self.make_patient(1, p)
        series = [  # day, source, sbp, dbp, meds
            # Climbing, every reading under 140/90 (PLAN.md demo step 3: no threshold crossed yet). Borderline
            # shows before the demo SMS; the trend fires on the SMS (138/88).
            (6, "clinic", 124, 78, "yes"), (22, "sms", 126, 80, "yes"), (40, "chw", 125, 79, "yes"),
            (61, "sms", 127, 80, "yes"), (82, "clinic", 128, 81, "yes"), (104, "sms", 130, 83, "yes"),
            (126, "chw", 132, 84, "yes"), (150, "sms", 135, 86, "yes"), (days - 4, "chw", 136, 87, "yes"),
        ]
        sw_reports = [e for e in self.examples.get("sw", []) if e["intent"] == "bp_report"
                      and not e["symptoms"] and e["meds_taken"] in ("yes", None)]
        for day, src, sbp, dbp, meds in series:
            if src == "clinic":
                rec = "Amlodipine 5 mg once daily. Reduce salt."
                eid = self.insert("encounters", patient_id=1, date=self.d(day), source="clinic",
                                  complaint_text="Nimekuja kupima presha", tests_performed=dumps(["bp", "blood_sugar"]),
                                  positive_results=dumps(["hypertension"]), referral=dumps([]),
                                  doctor_recommendation_text=rec, doctor_notes_text="Stable. Continue.",
                                  bp_text=f"{sbp}/{dbp}", sbp=sbp, dbp=dbp, blood_sugar=5.6,
                                  blood_sugar_unit="mmol/L", meds_taken=meds, symptom_codes=dumps([]), lang="en")
                self.insert("medications", patient_id=1, encounter_id=eid, name="amlodipine",
                            dose_text="5 mg once daily", source_text=rec)
                self.insert("followups", patient_id=1, due_date=self.d(day), kind="clinic_appointment",
                            status="done", done_on=self.d(day))
            elif src == "chw":
                self.insert("encounters", patient_id=1, date=self.d(day), source="chw",
                            chw_notes_text="Mgonjwa yuko sawa, anakunywa dawa.", bp_text=f"{sbp}/{dbp}",
                            sbp=sbp, dbp=dbp, meds_taken=meds, symptom_codes=dumps([]), lang="sw")
                self.log_visit(1, p, day)
            else:
                ex = self.rng.choice(sw_reports) if sw_reports else {"template": "Presha yangu ni {sbp}/{dbp}"}
                text = ex["template"].format(sbp=sbp, dbp=dbp)
                eid = self.insert("encounters", patient_id=1, date=self.d(day), source="sms", complaint_text=text,
                                  bp_text=f"{sbp}/{dbp}", sbp=sbp, dbp=dbp, meds_taken=ex.get("meds_taken"),
                                  symptom_codes=dumps([]), lang="sw")
                self.insert("messages", patient_id=1, direction="in", kind="report", lang="sw", text=text,
                            received_at=f"{self.d(day)}T07:30:00",
                            parsed_json=dumps({"intent": "bp_report", "valid": True, "confirmed": True,
                                               "fields": {"sbp": sbp, "dbp": dbp}, "backend": "synthetic"}),
                            encounter_id=eid, status=None)
        # The next clinic appointment in 12 days and a CHW visit due in 3 weeks. No missed appointment, so she is
        # medium before the demo SMS whether or not the peer-trajectory flag fires, and high after it.
        self.insert("followups", patient_id=1, due_date=self.d(days - 1 + 12), kind="clinic_appointment", status="due")
        self.insert("followups", patient_id=1, due_date=self.d(days - 4 + 21), kind="chw_visit", status="due")
        # Quiz history: medication and salt answered correctly, so danger signs come next.
        for q in self.quiz:
            if q["topic"] in ("medication", "salt_and_diet"):
                asked = days - self.rng.randint(8, 20)
                self.insert("quiz_results", patient_id=1, question_id=q["id"], topic=q["topic"],
                            asked_at=f"{self.d(asked)}T09:00:00", channel="sms", answer=str(q["correct"]),
                            correct="yes", lang="sw", next_due=self.d(asked + self.cfg["quiz"]["repeat_days_right"]))
        self.insert("synthetic_truth", patient_id=1, trajectory="drifting", deteriorates_within_30d="no",
                    groups_json=dumps(["demo"]))
        self.count("demo")

    # ----- extras for the other patients -----

    def finish_patient(self, pid, p):
        rng = self.rng
        days = p["days"]
        last_day = days - 1
        if p["trajectory"] == "event":
            etype = rng.choice(["stroke", "hospital_admission", "hospital_admission"])
            self.insert("events", patient_id=pid, date=self.d(p["event_day"]), type=etype,
                        source=rng.choice(["clinic", "referral"]), note="Synthetic event")
            # The CHW visit just before the event: high BP and, for a stroke, a symptom.
            self.chw_visit(pid, p, p["event_day"] - 2,
                           symptoms=["headache"] if rng.random() < 0.6 else [], note="Patient complains of headache.")
        if p["trajectory"] == "deteriorating_now":
            sym = [p["emergency_code"]] if "emergency_code" in p else (["headache"] if rng.random() < 0.5 else [])
            note = EMERGENCY_NOTES.get(p.get("emergency_code"), "Feeling unwell, headache.")
            self.chw_visit(pid, p, last_day - rng.randint(0, 2), symptoms=sym, note=note)
        elif "emergency_code" in p:
            self.chw_visit(pid, p, last_day - 1, symptoms=[p["emergency_code"]],
                           note=EMERGENCY_NOTES[p["emergency_code"]])

        # Quiz history for talkative patients.
        if self.quiz and "silent" not in p["groups"] and rng.random() < 0.6 or "low_understanding" in p["groups"]:
            asked_qs = rng.sample(self.quiz, min(len(self.quiz), rng.randint(1, 4)))
            if "low_understanding" in p["groups"]:
                asked_qs = [q for q in self.quiz if q["topic"] == "danger_signs"][:2] or asked_qs
            for q in asked_qs:
                asked = rng.randint(max(0, days - 80), last_day)
                wrong = "low_understanding" in p["groups"] or rng.random() < 0.25
                answer = q["correct"] % 3 + 1 if wrong else q["correct"]
                rep = self.cfg["quiz"]["repeat_days_wrong" if wrong else "repeat_days_right"]
                lang = p["language"]
                self.insert("quiz_results", patient_id=pid, question_id=q["id"], topic=q["topic"],
                            asked_at=f"{self.d(asked)}T10:00:00", channel="sms", answer=str(answer),
                            correct="no" if wrong else "yes", lang=lang, next_due=self.d(asked + rep))
                qtext = q["text"].get(lang) or q["text"]["en"]
                self.insert("messages", patient_id=pid, direction="out", kind="quiz", lang=lang, text=qtext,
                            received_at=f"{self.d(asked)}T10:00:00", status="sent",
                            parsed_json=dumps({"question_id": q["id"]}))

        truth = {"stable": "stable", "borderline": "borderline", "drifting": "drifting",
                 "deteriorating_now": "deteriorating", "event": "deteriorating"}[p["trajectory"]]
        self.insert("synthetic_truth", patient_id=pid, trajectory=truth,
                    deteriorates_within_30d="yes" if p["trajectory"] == "deteriorating_now" else "no",
                    groups_json=dumps(p["groups"] + (["event"] if p["trajectory"] == "event" else [])))
        self.count(p["trajectory"])
        for g in p["groups"]:
            self.count(g)

    def build_former(self, pid, days, has_event):
        """A former patient from the same villages, followed during the 12 months before the active window.
        With an event: blood pressure climbs for the last weeks, then a stroke or admission ends the series.
        Without: stable or drifting, then the patient moved away. Kept for the peer-trajectory history only."""
        rng = self.rng
        # Day 0 is the start of the active window, so negative days are the 6 months before it.
        day0 = -days + rng.randint(0, 40)
        end_day = rng.randint(day0 + 160, days - 60)  # ends at least 60 days before today
        p = {"status": "former", "groups": ["former"], "days": days, "day0": day0,
             "sex": rng.choice(["male", "female"]), "age": rng.randint(42, 80),
             "smoker": "yes" if rng.random() < 0.3 else "no", "village": rng.choice(list(VILLAGES)),
             "language": rng.choices(["sw", "rn", "fr", "en"], weights=[50, 25, 20, 5])[0],
             "base_sbp": max(124, min(152, rng.gauss(139, 7))),
             "drugs": rng.sample(["amlodipine", "hydrochlorothiazide", "enalapril", "nifedipine"], 1)}
        p["base_dbp"] = p["base_sbp"] * 0.63 + rng.gauss(0, 3)
        if has_event:
            # Some events follow a clear climb, some a subtle one: the model should not find it too easy.
            p.update(trajectory="event", event_day=end_day, ramp=rng.uniform(12, 42))
            if rng.random() < 0.4:
                p["groups"].append("missed_meds")
        else:
            p.update(trajectory=rng.choice(["stable", "stable", "drifting"]), rate=rng.uniform(0.3, 0.8))
        self.make_patient(pid, p)

        day = day0 + rng.randint(0, 20)
        while day < end_day:
            self.clinic(pid, p, day)
            self.insert("followups", patient_id=pid, due_date=self.d(day), kind="clinic_appointment",
                        status="done", done_on=self.d(day))
            day += rng.randint(60, 90)
        day = day0 + rng.randint(3, 14)
        while day < end_day - 2:
            self.chw_visit(pid, p, day)
            day += rng.randint(14, 28)
        if has_event:
            self.chw_visit(pid, p, end_day - 2, symptoms=["headache"] if rng.random() < 0.5 else [],
                           note="Patient complains of headache.")
            self.insert("events", patient_id=pid, date=self.d(end_day),
                        type=rng.choice(["stroke", "hospital_admission", "hospital_admission"]),
                        source=rng.choice(["clinic", "referral"]), note="Synthetic event (former patient)")
        truth = "deteriorating" if has_event else p["trajectory"]
        self.insert("synthetic_truth", patient_id=pid, trajectory=truth, deteriorates_within_30d="no",
                    groups_json=dumps(p["groups"] + (["event"] if has_event else [])))
        self.count("former_with_event" if has_event else "former_no_event")

    # ----- text-modifier cases (PLAN.md step 2) -----

    # kind, rule default it must produce, BP, symptoms, where the text goes, texts, direction, intended action
    # Each phrasing carries its gold context codes and quotes (PLAN.md 5b): [(code, exact quote), ...].
    MODIFIERS = [
        ("exertion", "visit_this_week", (166, 102), [], "note", [
            ("BP taken right after she walked up the hill carrying water. Repeat after 10 minutes of rest: 138/86.",
             [("measured_after_exertion", "BP taken right after she walked up the hill carrying water"),
              ("repeat_reading_normal", "Repeat after 10 minutes of rest: 138/86")]),
            ("Tension prise juste après une longue marche avec de l'eau. Contrôle après 10 minutes de repos : 136/84.",
             [("measured_after_exertion", "Tension prise juste après une longue marche"),
              ("repeat_reading_normal", "Contrôle après 10 minutes de repos : 136/84")]),
            ("He had just come back from digging in the field when I measured. Rested 15 minutes, second reading 134/84.",
             [("measured_after_exertion", "He had just come back from digging in the field"),
              ("repeat_reading_normal", "Rested 15 minutes, second reading 134/84")]),
            ("Amepima mara baada ya kubeba kuni kutoka msituni. Baada ya kupumzika, presha ilikuwa 137/85.",
             [("measured_after_exertion", "Amepima mara baada ya kubeba kuni"),
              ("repeat_reading_normal", "Baada ya kupumzika, presha ilikuwa 137/85")])],
         "down", "recheck_7_days"),
        ("pills_ran_out", "routine", (146, 90), [], "message", [
            ("Dawa zangu ziliisha siku kumi zilizopita, sijapata nyingine.",
             [("ran_out_of_pills", "Dawa zangu ziliisha siku kumi zilizopita")]),
            ("Je n'ai plus de comprimés depuis dix jours, la pharmacie est loin.",
             [("ran_out_of_pills", "Je n'ai plus de comprimés depuis dix jours")]),
            ("Sina vidonge tena tangu wiki iliyopita, sina pesa ya kununua.",
             [("ran_out_of_pills", "Sina vidonge tena tangu wiki iliyopita")]),
            ("My tablets finished last week and the clinic had none left.",
             [("ran_out_of_pills", "My tablets finished last week")])],
         "up", "recheck_7_days"),
        ("travelling", "visit_this_week", (164, 101), [], "note", [
            ("Patient leaves tomorrow to visit family in Bujumbura for two weeks; has a phone and replies to SMS.",
             [("travelling", "leaves tomorrow to visit family in Bujumbura for two weeks")]),
            ("Mgonjwa anasafiri kwenda Bujumbura kwa wiki mbili; ana simu na anajibu SMS.",
             [("travelling", "anasafiri kwenda Bujumbura kwa wiki mbili")]),
            ("Away at a funeral in Gitega until the end of the month, reachable by text.",
             [("travelling", "Away at a funeral in Gitega until the end of the month")]),
            ("Elle part chez sa fille à Ngozi pour deux semaines, elle répond aux SMS.",
             [("travelling", "Elle part chez sa fille à Ngozi pour deux semaines")])],
         "down", "recheck_7_days"),
        ("lives_alone", "recheck_7_days", (132, 82), ["headache"], "note", [
            ("Lives alone, cannot read, does not use the phone for messages.",
             [("lives_alone", "Lives alone"), ("cannot_read", "cannot read")]),
            ("Vit seule, ne sait pas lire, n'utilise pas les SMS.",
             [("lives_alone", "Vit seule"), ("cannot_read", "ne sait pas lire")]),
            ("Widower living by himself; his phone is broken and he cannot read texts anyway.",
             [("lives_alone", "living by himself"), ("unreachable", "his phone is broken"),
              ("cannot_read", "he cannot read texts")]),
            ("Anaishi peke yake, hajui kusoma, hana simu.",
             [("lives_alone", "Anaishi peke yake"), ("cannot_read", "hajui kusoma"), ("unreachable", "hana simu")])],
         "up", "visit_this_week"),
        ("chest_pain_sms", "routine", (136, 84), [], "message_unextracted", [
            ("Kifua kinauma sana tangu asubuhi na napumua kwa shida.", [("new_symptom", "Kifua kinauma sana")]),
            ("Strong pain in my chest since this morning, it is hard to breathe.", [("new_symptom", "Strong pain in my chest")]),
            ("J'ai une forte douleur dans la poitrine qui va dans le bras gauche.",
             [("new_symptom", "une forte douleur dans la poitrine")]),
            ("Moyo unauma sana na jasho linatoka, sijisikii vizuri kabisa.", [("new_symptom", "Moyo unauma sana")])],
         "up", "emergency_now"),
        ("side_effect", "visit_this_week", (166, 101), [], "note", [
            ("Feet and ankles swollen since the new tablets started two weeks ago.",
             [("side_effect", "Feet and ankles swollen since the new tablets started")]),
            ("Pieds et chevilles gonflés depuis le début du nouveau médicament.",
             [("side_effect", "Pieds et chevilles gonflés depuis le début du nouveau médicament")]),
            ("Legs puffy up to the knees since the doctor changed her medicine.",
             [("side_effect", "Legs puffy up to the knees since the doctor changed her medicine")]),
            ("Miguu imevimba tangu aanze dawa mpya ya presha.", [("side_effect", "Miguu imevimba tangu aanze dawa mpya")])],
         "up", "refer_clinic"),
    ]

    def add_text_modifiers(self, plan, n_current, n_past):
        """Give n_current patients a note or message on their latest visit, and n_past patients one on an
        earlier visit, whose right action differs from the rule default (PLAN.md step 2). Each case is kept
        only if it really produces the intended rule default; otherwise the next patient is tried."""
        from engine import decide, signals
        rng = self.rng
        pool = [pid for pid, p in plan.items() if p["trajectory"] in ("stable", "drifting", "borderline")
                and not {"silent", "emergency_symptom"} & set(p["groups"])]
        rng.shuffle(pool)
        jobs = [("current", self.MODIFIERS[i % len(self.MODIFIERS)]) for i in range(n_current)]
        jobs += [("past", self.MODIFIERS[i % len(self.MODIFIERS)]) for i in range(n_past)]
        done = 0
        for when, (kind, want, (sbp, dbp), syms, where, texts, direction, action) in jobs:
            # Try the candidates in order; a patient who does not suit this kind stays in the pool for the next.
            for pid in list(pool):
                p = plan[pid]
                day = p["days"] - 1 - rng.randint(0, 2) if when == "current" else rng.randint(40, p["days"] - 60)
                # dev uses the first two phrasings, test the last two, so a held-out set never repeats a sentence.
                text, gold = rng.choice(texts[:2] if self.text_variant == "dev" else texts[2:])
                note = text if where == "note" else "Home visit, BP taken."
                eid = self.insert("encounters", patient_id=pid, date=self.d(day), source="chw", chw_notes_text=note,
                                  bp_text=f"{sbp}/{dbp}", sbp=sbp, dbp=dbp, meds_taken="yes",
                                  symptom_codes=dumps(syms), lang="en")
                mid = None
                if where.startswith("message"):
                    # Extraction left empty on purpose for the chest-pain case: only the text says it.
                    parsed = {"intent": "symptom" if where == "message_unextracted" else "meds_question",
                              "fields": {"sbp": None, "dbp": None, "meds_taken": None, "symptoms": []},
                              "valid": True, "confirmed": True, "backend": "synthetic"}
                    mid = self.insert("messages", patient_id=pid, direction="in", kind="other", lang="sw", text=text,
                                      received_at=f"{self.d(day)}T18:00:00", parsed_json=dumps(parsed), status=None)
                sig = signals.compute(self.con, self.cfg, pid, self.d(day))
                emergency = any(f["emergency"] == "yes" for f in sig["flags"])
                if not emergency and decide.rule_action(self.cfg, sig["flags"]) == want:
                    self.con.execute(
                        "UPDATE synthetic_truth SET text_modifier = ?, text_modifier_reason = ?, text_modifier_action = ?, "
                        "text_modifier_encounter = ?, text_modifier_date = ?, text_modifier_codes_json = ? WHERE patient_id = ?",
                        (direction, f"{kind}: {text}", action, eid, self.d(day),
                         dumps([{"code": c, "quote": q} for c, q in gold]), pid))
                    self.count(f"text_modifier_{when}")
                    done += 1
                    pool.remove(pid)
                    break
                # Not the intended rule default for this patient: undo and try the next one.
                self.con.execute("DELETE FROM encounters WHERE id = ?", (eid,))
                if mid:
                    self.con.execute("DELETE FROM messages WHERE id = ?", (mid,))
        self.con.execute("UPDATE synthetic_truth SET text_modifier = 'none' WHERE text_modifier IS NULL")
        return done

    def add_review_signals(self, n_corrected=4, n_sent_home=2):
        """Evidence for the error-review queue (PLAN.md step 11c): SMS whose extracted fields the CHW corrected
        before confirming, and referrals the clinic sent home with no change."""
        rng = self.rng
        msgs = self.con.execute("SELECT m.id, m.parsed_json FROM messages m JOIN patients p ON p.id = m.patient_id "
                                "WHERE m.direction = 'in' AND m.kind = 'report' AND m.patient_id != 1 "
                                "AND p.status = 'active' ORDER BY m.id").fetchall()
        for m in rng.sample(msgs, min(n_corrected, len(msgs))):
            pj = loads(m["parsed_json"], {}) or {}
            true = dict(pj.get("fields") or {})
            wrong = dict(true)
            if rng.random() < 0.5 and true.get("dbp"):
                wrong["dbp"] = true["dbp"] + rng.choice([-10, 10])  # a misread diastolic
            else:
                wrong["symptoms"] = []  # a symptom the extraction missed
                true["symptoms"] = sorted(set(true.get("symptoms") or []) | {"dizziness"})
            pj.update(fields=wrong, confirmed_fields=true, llm={"model": self.cfg["llm"]["model"]})
            self.con.execute("UPDATE messages SET parsed_json = ? WHERE id = ?", (dumps(pj), m["id"]))
        visits = self.con.execute("SELECT id, date FROM decisions WHERE suggestion = 'visit_this_week' "
                                  "AND patient_id != 1 ORDER BY id").fetchall()
        for d in rng.sample(visits, min(n_sent_home, len(visits))):
            self.con.execute("UPDATE decisions SET final_choice = 'refer_clinic', override_reason = ?, "
                             "outcome_at_next_contact = ? WHERE id = ?",
                             ("CHW worried, sent to clinic", f"referral: sent_home ({d['date']})", d["id"]))
        self.count("review_signals")

    def admin_time(self, n_chw=1):
        """One hour of admin per week for each CHW, every Monday morning."""
        rows = []
        total = (self.end - self.start).days
        for day in range(total):
            d = self.start + timedelta(days=day)
            if d.weekday() == 0:
                for chw in range(1, n_chw + 1):
                    rows.append((chw, f"{d.isoformat()}T07:00:00", f"{d.isoformat()}T08:00:00"))
        self.con.executemany('INSERT INTO time_log (chw_id, activity, start, "end") VALUES (?, \'admin\', ?, ?)', rows)

    def assign_chws(self, plan, per_chw):
        """One laptop, many CHWs: each CHW follows about per_chw patients from neighbouring villages.
        The demo patient (Kirundo, patient 1) goes to CHW 1. Returns the number of CHWs."""
        order = list(VILLAGES)
        ids = sorted(plan, key=lambda pid: (order.index(plan[pid]["village"]), pid))
        if 1 not in plan:
            ids = [1] + ids  # the demo patient first, so CHW 1 has them
        for i, pid in enumerate(ids):
            if pid in plan:
                plan[pid]["chw_id"] = i // per_chw + 1
        return max(1, -(-len(ids) // per_chw))

    def past_decisions(self, pids):
        """Past suggestions and the CHW's choices after each CHW visit in the last 90 days.
        The suggestion is the config rule; about 1 in 7 is overridden."""
        from engine import decide, signals
        rng = self.rng
        since = (self.end - timedelta(days=90)).isoformat()
        rows = self.con.execute(
            "SELECT e.patient_id, e.date FROM encounters e JOIN patients p ON p.id = e.patient_id "
            "WHERE e.source = 'chw' AND e.date >= ? AND p.status = 'active' ORDER BY e.date",
            (since,),
        ).fetchall()
        actions = [a for a in self.cfg["actions"] if a not in ("not_sure", "emergency_now")]
        for r in rows:
            sig = signals.compute(self.con, self.cfg, r["patient_id"], r["date"])
            text, state = decide.build_state(self.con, self.cfg, r["patient_id"], r["date"], sig)
            emergency = any(f["emergency"] == "yes" for f in sig["flags"])
            rule = "emergency_now" if emergency else decide.rule_action(self.cfg, sig["flags"])
            final, reason = rule, None
            if not emergency and rng.random() < 0.15:
                final = rng.choice([a for a in actions if a != rule])
                reason = rng.choice(OVERRIDE_REASONS)
            self.insert("decisions", patient_id=r["patient_id"], date=r["date"],
                        state_json=dumps({"text": text, **state}), suggestion=rule,
                        probs_json=dumps({rule: 1.0}), final_choice=final, override_reason=reason,
                        model_version="synthetic-history", config_version=self.cfg["config_version"])


def main():
    parser = argparse.ArgumentParser(description="Fill the database with synthetic patients.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", default="config/guideline_htn.json")
    parser.add_argument("--content", default="content", help="folder with i18n/ and quiz/")
    parser.add_argument("--patients", type=int, default=60)
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--end-date", default=None, help="last day of the data, YYYY-MM-DD (default today)")
    parser.add_argument("--chw", type=int, default=1)
    parser.add_argument("--former-patients", type=int, default=30,
                        help="past patients (12 months before the active window) for the peer-trajectory history")
    parser.add_argument("--former-event-share", type=float, default=0.6,
                        help="share of former patients whose follow-up ended with a stroke or admission")
    parser.add_argument("--patients-per-chw", type=int, default=150,
                        help="patients each CHW follows (one clinic laptop, many CHWs)")
    parser.add_argument("--text-variant", choices=["dev", "test"], default="dev",
                        help="which phrasings the text-modifier notes use: dev (development set) or test (held-out set)")
    parser.add_argument("--text-modifiers", type=int, default=12,
                        help="patients whose latest note or message should move the action one step")
    parser.add_argument("--text-modifier-episodes", type=int, default=18,
                        help="patients with such a note or message on an earlier visit (more labeled rows)")
    args = parser.parse_args()

    # Create the tables first if this is a new database file.
    from data.make_db import make_db
    make_db(root_path(args.db), root_path("data/schema.sql"))
    con = connect(args.db)
    have = {r[1] for r in con.execute("PRAGMA table_info(synthetic_truth)")}
    for col in ("text_modifier", "text_modifier_reason", "text_modifier_action", "text_modifier_encounter",
                "text_modifier_date", "text_modifier_codes_json"):
        if col not in have:
            con.execute(f"ALTER TABLE synthetic_truth ADD COLUMN {col} TEXT")
    cfg = load_config(args.config)
    content = root_path(args.content)
    rng = random.Random(args.seed)
    end = date.fromisoformat(args.end_date) if args.end_date else date.today()
    examples = {lang: load_examples(content, lang) for lang in ("sw", "fr", "en")}
    if not examples["sw"]:
        print("Warning: content/i18n/sw_examples.json not found; patients will have no SMS reports.")
    quiz = load_quiz(content)

    for t in TABLES:  # every synthetic table; the users table (PINs) is kept
        if con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (t,)).fetchone():
            con.execute(f"DELETE FROM {t}")

    b = Builder(con, cfg, rng, end, args.months, examples, quiz, args.chw, args.text_variant)
    days = args.months * 30
    if args.text_variant == "dev":
        b.build_demo(days)  # the held-out test set has no hand-built patient
    plan = b.plan_patients(args.patients, args.months)
    n_chw = b.assign_chws(plan, args.patients_per_chw)
    for pid in sorted(plan):
        b.build_patient(pid, plan[pid])
        b.finish_patient(pid, plan[pid])
    n_event = round(args.former_patients * args.former_event_share)
    for i in range(args.former_patients):
        b.build_former(args.patients + 1 + i, days, has_event=i < n_event)
    b.admin_time(n_chw)
    con.commit()
    b.add_text_modifiers(plan, args.text_modifiers, args.text_modifier_episodes)
    con.commit()
    b.past_decisions(list(plan))
    b.add_review_signals()

    set_meta(con, "synthetic", "true")
    set_meta(con, "schema_version", "1")
    set_meta(con, "config_version", cfg["config_version"])
    set_meta(con, "synthetic_end_date", end.isoformat())
    set_meta(con, "seed", args.seed)
    con.commit()

    # Store today's flags for every patient so the dashboard has them at once.
    from engine import signals
    for (pid,) in con.execute("SELECT id FROM patients").fetchall():
        signals.refresh(con, cfg, pid, end.isoformat())

    print(f"Synthetic database: {args.db}  (end date {end}, {args.months} months, seed {args.seed})")
    active = con.execute("SELECT COUNT(*) FROM patients WHERE status = 'active'").fetchone()[0]
    print(f"  active patients {active}, former patients {args.former_patients} (history only), "
          f"{n_chw} CHW(s), {len(VILLAGES)} villages")
    for table in ["patients", "encounters", "messages", "medications", "followups", "events",
                  "quiz_results", "decisions", "time_log", "flags"]:
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<14} {n}")
    print("Groups (a patient can be in several):")
    for g, n in sorted(b.summary.items()):
        print(f"  {g:<20} {n}")
    con.close()


if __name__ == "__main__":
    main()
