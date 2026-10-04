"""Step 2b. Labeled synthetic sets, so every model gets a number.

Makes three files in --out:
1. sms_labeled.csv: 300 synthetic SMS in Swahili, French and English from the content templates, with
   gold intent and gold fields known by construction; 60 messy ones ("150 and 95", typos, mixed language)
   and 30 with no numbers.
2. decisions_labeled.csv: for every synthetic encounter, the state string, the gold action from the
   config rules, the 30-day deterioration label, and the risk components.
3. physician_sample.csv: 50 rows from (2) with an empty physician_action column, filled by hand.

Labels made from the config rules test that the pipeline follows the rules. The physician sample is the
only clinical check. None of this is a clinical validation.

Usage:
    python data/make_labels.py --db data/chw.db --out data/labels --seed 1
"""

import argparse
import json
import os
import random
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import decide, risk, signals
from engine.common import add_days, connect, dumps, get_meta, load_config, root_path

MIXED = {"sw": ["merci", "thank you", "ok"], "fr": ["asante", "thanks", "sawa"], "en": ["asante", "merci", "sawa"]}
JOINERS = {"sw": ["{sbp} na {dbp}", "{sbp} {dbp}", "{sbp}-{dbp}", "juu {sbp} chini {dbp}"],
           "fr": ["{sbp} et {dbp}", "{sbp} {dbp}", "{sbp}-{dbp}", "{sbp} sur {dbp}"],
           "en": ["{sbp} and {dbp}", "{sbp} {dbp}", "{sbp}-{dbp}", "{sbp} over {dbp}"]}


def load_examples(lang):
    with open(root_path(f"content/i18n/{lang}_examples.json"), encoding="utf-8") as f:
        return json.load(f)["examples"]


def typo(rng, text):
    """Swap or drop letters in a few words. Digits are never touched."""
    words = text.split(" ")
    for _ in range(max(1, len(words) // 4)):
        i = rng.randrange(len(words))
        w = words[i]
        if len(w) > 3 and w.isalpha():
            j = rng.randrange(len(w) - 1)
            words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:] if rng.random() < 0.5 else w[:j] + w[j + 1:]
    return " ".join(words)


def make_sms(rng, n_total=300, n_messy=60, n_none=30):
    examples = {lang: load_examples(lang) for lang in ("sw", "fr", "en")}
    langs = ["sw", "sw", "fr", "en"]  # Swahili first: it is the demo language
    rows = []

    tid = {(lang, e["template"]): f"{lang}-{i:02d}" for lang in examples for i, e in enumerate(examples[lang])}

    def fill(ex, lang, messy):
        sbp, dbp = rng.randint(105, 195), rng.randint(62, 118)
        t = ex["template"]
        if messy and "{sbp}" in t:
            for pat in ("{sbp}/{dbp}", "{sbp} kwa {dbp}", "{sbp} sur {dbp}", "{sbp} over {dbp}"):
                if pat in t:
                    t = t.replace(pat, rng.choice(JOINERS[lang]))
                    break
        text = t.format(sbp=sbp, dbp=dbp) if "{sbp}" in t else t
        if messy:
            text = typo(rng, text)
            if rng.random() < 0.5:
                text = text.lower()
            if rng.random() < 0.5:
                text = f"{text} {rng.choice(MIXED[lang])}"
        has_bp = "{sbp}" in ex["template"]
        return {"lang": lang, "template_id": tid[(lang, ex["template"])], "text": text, "intent": ex["intent"],
                "sbp": sbp if has_bp else None, "dbp": dbp if has_bp else None,
                "meds_taken": ex["meds_taken"], "symptoms": dumps(ex["symptoms"]),
                "messy": int(messy), "no_numbers": int(not has_bp)}

    for _ in range(n_none):
        lang = rng.choice(langs)
        ex = rng.choice([e for e in examples[lang] if "{sbp}" not in e["template"]])
        rows.append(fill(ex, lang, False))
    for _ in range(n_messy):
        lang = rng.choice(langs)
        ex = rng.choice([e for e in examples[lang] if "{sbp}" in e["template"]])
        rows.append(fill(ex, lang, True))
    while len(rows) < n_total:
        lang = rng.choice(langs)
        rows.append(fill(rng.choice(examples[lang]), lang, False))
    rng.shuffle(rows)
    for i, r in enumerate(rows, 1):
        r["id"] = i
    return pd.DataFrame(rows)[["id", "lang", "template_id", "text", "intent", "sbp", "dbp", "meds_taken",
                               "symptoms", "messy", "no_numbers"]]


# The generator's own missed-medicine notes, complaints and SMS (data/make_synthetic.py): a row whose recent notes
# or messages contain one of these is a text-modifier row ("up") by construction. Never taken from the phrase list
# the decider uses, so the test is not circular (PLAN.md 5b, decided 4 October).
def _missed_templates():
    from data.make_synthetic import CHW_NOTES
    return set(CHW_NOTES["missed"]) | {"Dawa zimeisha", "Needs refill of medicine", "Renouvellement du traitement"}


def _missed_meds_evidence(con, cfg, patient_id, day, notes):
    """The generator note, complaint or SMS (meds_taken no or partial by construction) in the decider's window."""
    since = add_days(day, -cfg.get("context_window_days", 30))
    for r in con.execute("SELECT chw_notes_text, complaint_text FROM encounters WHERE patient_id = ? AND date <= ? "
                         "AND date >= ? ORDER BY date DESC, id DESC LIMIT 2", (patient_id, day + "T99", since)).fetchall():
        for t in (r["chw_notes_text"], r["complaint_text"]):
            if t and t in notes:
                return t
    for r in con.execute("SELECT text, parsed_json FROM messages WHERE patient_id = ? AND direction = 'in' AND kind != 'quiz' "
                         "AND substr(received_at, 1, 10) <= ? AND substr(received_at, 1, 10) >= ? "
                         "ORDER BY received_at DESC, id DESC LIMIT 3", (patient_id, day, since)).fetchall():
        fields = (json.loads(r["parsed_json"] or "{}") or {}).get("fields") or {}
        if fields.get("meds_taken") in ("no", "partial"):
            return r["text"]
    return None


LADDER = ["routine", "recheck_7_days", "visit_this_week", "refer_clinic"]


COMPONENTS = ["threshold_flag", "trend_flag", "symptom_flag", "missed_meds", "silent_30_days",
              "overdue_followup", "missed_appointment", "low_understanding"]


def make_decisions(con, cfg):
    end = get_meta(con, "synthetic_end_date")
    truth_rows = {r["patient_id"]: dict(r) for r in con.execute("SELECT * FROM synthetic_truth").fetchall()}
    truth = {pid: r["deteriorates_within_30d"] for pid, r in truth_rows.items()}
    events = {}
    for r in con.execute("SELECT patient_id, date FROM events").fetchall():
        events.setdefault(r["patient_id"], []).append(r["date"][:10])
    rows = []
    missed_notes = _missed_templates()
    encs = con.execute("SELECT id, patient_id, date, source FROM encounters ORDER BY patient_id, date, id").fetchall()
    for e in encs:
        day = e["date"][:10]
        sig = signals.compute(con, cfg, e["patient_id"], day)
        text, state = decide.build_state(con, cfg, e["patient_id"], day, sig)
        emergency = any(f["emergency"] == "yes" for f in sig["flags"])
        rule_default = "emergency_now" if emergency else decide.rule_action(cfg, sig["flags"])
        t = truth_rows.get(e["patient_id"], {})
        is_mod = t.get("text_modifier_encounter") is not None and int(t["text_modifier_encounter"]) == e["id"]
        # Gold: the rule default, or for a text-modifier encounter the action the note or message calls for
        # (the rule default moved one step in the modifier's direction; emergency_now for unextracted chest pain).
        gold = t["text_modifier_action"] if is_mod else rule_default
        source = "case" if is_mod else ""
        if not is_mod and rule_default in LADDER[:-1]:
            ev = _missed_meds_evidence(con, cfg, e["patient_id"], day, missed_notes)
            if ev:
                is_mod, source = True, "note"
                gold = LADDER[LADDER.index(rule_default) + 1]
                t = {**t, "text_modifier": "up", "text_modifier_reason": f"missed_meds (generator note or message): {ev}"}
        soon = add_days(day, 30)
        det = any(day < ev <= soon for ev in events.get(e["patient_id"], []))
        if truth.get(e["patient_id"]) == "yes" and end and day >= add_days(end, -30):
            det = True
        r = risk.score_patient(con, cfg, e["patient_id"], day, None, sig)
        row = {"encounter_id": e["id"], "patient_id": e["patient_id"], "date": day, "source": e["source"],
               "state": text, "rule_default": rule_default,
               "text_modifier": t.get("text_modifier") if is_mod else "none", "modifier_source": source,
               "text_modifier_reason": t.get("text_modifier_reason") if is_mod else "",
               "gold_context_codes": (t.get("text_modifier_codes_json") or "[]") if is_mod else "[]",
               "gold_action": gold, "deteriorates_30d": int(det), "score": r["score"],
               "latest_sbp": sig["latest"]["sbp"] if sig["latest"] else None,
               "change_sbp": sig["change_sbp"], "slope_sbp": sig["slope_sbp"]}
        for c in COMPONENTS:
            row[c] = r["components"].get(c, {}).get("points", 0)
        rows.append(row)
    return pd.DataFrame(rows)


NEUTRAL_TEXTS = [  # CHW notes and messages where nothing should change the decision: precision check
    "Patient well, taking medicine.", "Mgonjwa yuko sawa, anakunywa dawa.", "Patient bien, prend ses médicaments.",
    "Home visit, no complaints.", "Home visit, BP taken.", "Asante sana kwa ujumbe", "Bonjour, merci pour le message",
    "Thank you for checking on me", "Nimekunywa dawa zote leo", "J'ai pris tous mes médicaments ce matin",
]


def make_codes(con):
    """PLAN.md 5b: the text of every text-modifier case with its gold context codes, plus neutral texts (no codes)."""
    rows = []
    for t in con.execute("SELECT * FROM synthetic_truth WHERE text_modifier != 'none'").fetchall():
        text = t["text_modifier_reason"].split(": ", 1)[1]
        rows.append({"patient_id": t["patient_id"], "kind": t["text_modifier_reason"].split(":", 1)[0], "text": text,
                     "gold_codes": t["text_modifier_codes_json"] or "[]"})
    for text in NEUTRAL_TEXTS:
        rows.append({"patient_id": None, "kind": "neutral", "text": text, "gold_codes": "[]"})
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="Make the labeled synthetic sets.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", default="config/guideline_htn.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--no-physician-sample", action="store_true",
                        help="skip physician_sample.csv (for the held-out test set: one sheet for the physician)")
    args = parser.parse_args()
    rng = random.Random(args.seed)
    out = root_path(args.out)
    os.makedirs(out, exist_ok=True)
    con = connect(args.db)
    cfg = load_config(args.config)

    # Which database these labels belong to: engine/evaluate.py refuses labels from another end date or seed.
    with open(os.path.join(out, "labels_meta.json"), "w") as f:
        json.dump({"synthetic_end_date": get_meta(con, "synthetic_end_date"), "seed": get_meta(con, "seed")}, f, indent=1)

    sms = make_sms(rng)
    sms.to_csv(os.path.join(out, "sms_labeled.csv"), index=False)
    print(f"sms_labeled.csv: {len(sms)} rows; by language {sms['lang'].value_counts().to_dict()}; "
          f"messy {int(sms['messy'].sum())}; no numbers {int(sms['no_numbers'].sum())}")

    dec = make_decisions(con, cfg)
    dec.to_csv(os.path.join(out, "decisions_labeled.csv"), index=False)
    print(f"decisions_labeled.csv: {len(dec)} rows; gold actions {dec['gold_action'].value_counts().to_dict()}; "
          f"deteriorates_30d {int(dec['deteriorates_30d'].sum())}")

    codes = make_codes(con)
    codes.to_csv(os.path.join(out, "codes_labeled.csv"), index=False)
    print(f"codes_labeled.csv: {len(codes)} texts; {int((codes['gold_codes'] != '[]').sum())} with gold context codes")

    if args.no_physician_sample:
        return
    # Physician sample: every text-modifier row (PLAN.md asks for at least 30), then plain rows spread over
    # the actions, 50 in total. The physician fills physician_action without seeing the suggestion.
    mods = dec[dec["modifier_source"] == "case"]  # the 30 deliberate cases first
    if len(mods) < 30:
        print(f"Note: only {len(mods)} text-modifier rows; PLAN.md asks for at least 30 in the physician sample.")
    plain = dec[dec["modifier_source"] != "case"]
    per = max(1, (50 - len(mods)) // max(1, plain["gold_action"].nunique()))
    parts = [g.sample(min(len(g), per), random_state=args.seed) for _, g in plain.groupby("gold_action")]
    sample = pd.concat([mods] + parts)
    rest = plain.drop(pd.concat(parts).index)
    sample = pd.concat([sample, rest.sample(max(0, 50 - len(sample)), random_state=args.seed)]).head(50)
    sample = sample.sample(frac=1, random_state=args.seed)  # mixed order, so modifier rows are not grouped
    sample = sample[["encounter_id", "patient_id", "date", "state"]].copy()
    # The physician sees the flags, notes and messages, not the rule default or which rows are modifier cases.
    sample["state"] = sample["state"].str.replace(r"\nRule default: [a-z_0-9]+\.", "", regex=True)
    sample["physician_action"] = ""
    sample["physician_note"] = ""
    path = os.path.join(out, "physician_sample.csv")
    if os.path.exists(path):
        old = pd.read_csv(path)
        if old["physician_action"].notna().any():
            print(f"physician_sample.csv already has physician answers; not overwritten.")
            return
    sample.to_csv(path, index=False)
    print(f"physician_sample.csv: {len(sample)} rows; fill the physician_action column with one of {cfg['actions']}")


if __name__ == "__main__":
    main()
