"""Step 2b. Evaluation: one number for every model, printed as a table and saved to docs/evaluation.md.

1. SMS intent on sms_labeled.csv, accuracy per language (Julia-1 when installed; otherwise the
   TF-IDF fallback, trained on a split and scored on held-out rows).
2. llm.extract on sms_labeled.csv: field accuracy, and the rejection rate on messy messages.
3. decide.py on decisions_labeled.csv: agreement with the config rules, and with the physician column where filled.
4. retrain.py with a held-out split: next-action and 30-day deterioration scores.
5. Peer trajectories: leave one patient out, AUC and share flagged.

These labels are synthetic and made from the config rules: they test that the pipeline follows the rules.
The physician sample is the only clinical check. None of this is a clinical validation.

Usage:
    python engine/evaluate.py --db data/chw.db --labels data/labels --config config/guideline_htn.json [--llm-limit 300]
"""

import argparse
import json
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import decide, llm, models, retrain, trajectory
from engine.common import ROOT, connect, get_meta, load_config, loads, root_path


def eval_intent_independent(cfg, train_sms, test_sms, seed=1):
    """Independent test: TF-IDF trains on development-set SMS from some templates, and is scored on held-out-set
    SMS from the other templates (different numbers, typos and patients, and templates never seen)."""
    import random as _r
    templates = sorted(set(train_sms["template_id"]) | set(test_sms["template_id"]))
    rnd = _r.Random(seed)
    held = set(rnd.sample(templates, round(len(templates) * 0.3)))
    tr = train_sms[~train_sms["template_id"].isin(held)]
    te = test_sms[test_sms["template_id"].isin(held)]
    m = models.train_text_model(tr["text"].tolist(), tr["intent"].tolist(), char_ngrams=True)

    def score(pred):
        t = te.assign(pred=list(pred))
        return {"accuracy": round(float((t["pred"] == t["intent"]).mean()), 3),
                "by_language": {lang: round(float((g["pred"] == g["intent"]).mean()), 3) for lang, g in t.groupby("lang")}}

    tf_probs = m.predict_proba(te["text"].tolist())
    tf_pred = m.classes_[tf_probs.argmax(axis=1)]
    out = {"n": len(te), "n_train": len(tr), "templates_unseen": int(te["template_id"].nunique()),
           "split": "development set, other templates -> held-out set", "tfidf": score(tf_pred),
           "mode": cfg.get("julia", {}).get("intent")}
    if models.julia(cfg) is not None:
        jl = [models.julia_choose(cfg, t, models.INTENT_QUESTION, models.INTENTS)[0] for t in te["text"]]
        out["julia"] = score(jl)
        below = cfg.get("julia", {}).get("intent_fallback_below", 0.7)
        mix = [j if p.max() < below else t for t, j, p in zip(tf_pred, jl, tf_probs)]
        out["in_use_mix"] = {**score(mix), "julia_used_on": int(sum(p.max() < below for p in tf_probs))}
    return out


def eval_retrain_independent(train_labels, test_labels):
    """Independent test: the next-action and deterioration models train on the development labels and are
    scored on the held-out labels."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
    tr = pd.read_csv(os.path.join(train_labels, "decisions_labeled.csv"))
    te = pd.read_csv(os.path.join(test_labels, "decisions_labeled.csv"))
    m = models.train_text_model(tr["state"].astype(str).tolist(), tr["gold_action"].tolist())
    pred = m.predict(te["state"].astype(str).tolist())
    probs = m.predict_proba(te["state"].astype(str).tolist()).max(axis=1)
    dec = {"n_train": len(tr), "n_test": len(te), "accuracy": round(float(accuracy_score(te["gold_action"], pred)), 3),
           "macro_f1": round(float(f1_score(te["gold_action"], pred, average="macro", zero_division=0)), 3),
           "mean_top_probability": round(float(probs.mean()), 3)}
    feats = retrain.DET_FEATURES
    a, b = tr.fillna(0), te.fillna(0)
    lr = LogisticRegression(max_iter=1000, class_weight="balanced").fit(a[feats].values, a["deteriorates_30d"].values)
    auc = roc_auc_score(b["deteriorates_30d"], lr.predict_proba(b[feats].values)[:, 1]) if b["deteriorates_30d"].nunique() > 1 else None
    return {"decider": dec, "deterioration": {"n_train": len(a), "n_test": len(b), "positives": int(b["deteriorates_30d"].sum()),
                                              "auc_heldout_patients": round(float(auc), 3) if auc is not None else None}}


def eval_intent(cfg, sms, seed=1):
    """Leave-template-out: train TF-IDF on some message templates, test on templates it never saw.
    Julia-1 (zero-shot) and the mix in use (config.julia.intent) are scored on the same unseen rows."""
    from sklearn.model_selection import GroupShuffleSplit
    split = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=seed)
    tr_i, te_i = next(split.split(sms, sms["intent"], groups=sms["template_id"]))
    tr, te = sms.iloc[tr_i], sms.iloc[te_i]
    m = models.train_text_model(tr["text"].tolist(), tr["intent"].tolist(), char_ngrams=True)

    def score(pred):
        t = te.assign(pred=list(pred))
        return {"accuracy": round(float((t["pred"] == t["intent"]).mean()), 3),
                "by_language": {lang: round(float((g["pred"] == g["intent"]).mean()), 3) for lang, g in t.groupby("lang")}}

    tf_probs = m.predict_proba(te["text"].tolist())
    tf_pred = m.classes_[tf_probs.argmax(axis=1)]
    out = {"n": len(te), "n_train": len(tr), "templates_unseen": int(te["template_id"].nunique()),
           "split": "leave-template-out", "tfidf": score(tf_pred), "mode": cfg.get("julia", {}).get("intent")}
    if models.julia(cfg) is not None:
        jl = [models.julia_choose(cfg, t, models.INTENT_QUESTION, models.INTENTS)[0] for t in te["text"]]
        out["julia"] = score(jl)
        below = cfg.get("julia", {}).get("intent_fallback_below", 0.7)
        mix = [j if p.max() < below else t for t, j, p in zip(tf_pred, jl, tf_probs)]
        out["in_use_mix"] = {**score(mix), "julia_used_on": int(sum(p.max() < below for p in tf_probs))}
    return out


def eval_quiz_reply(cfg):
    """Free-text quiz replies: each option rephrased ("I think ...") must map to that option,
    and replies like "sijui" (I don't know) must come back unclear."""
    from engine import content, quiz
    if models.julia(cfg) is None:
        return None
    lead = {"en": "I think ", "sw": "Nadhani ", "fr": "Je pense "}
    right = n = unclear = n_unclear = 0
    for q in content.quiz_bank()["questions"]:
        for lang in lead:
            for i, o in enumerate(q["options"][lang], 1):
                got, _ = quiz._match_option(cfg, q, lang, lead[lang] + o.lower())
                right += got == i
                n += 1
        for junk, lang in (("sijui", "sw"), ("ok asante", "sw"), ("je ne sais pas", "fr")):
            got, _ = quiz._match_option(cfg, q, lang, junk)
            unclear += got is None
            n_unclear += 1
    return {"n": n, "mapped_right": round(right / n, 3), "unclear_right": round(unclear / n_unclear, 3),
            "backend": "julia-1" if models.julia_on(cfg, "quiz_reply") else "word-match"}


def _same(a, b):
    if pd.isna(a) and b is None:
        return True
    if pd.isna(a) or b is None:
        return False
    return str(a) == str(b) if not isinstance(b, (int, float)) else float(a) == float(b)


def eval_extract(cfg, sms, limit):
    if not llm.reachable():
        return {"note": "Ollama not reachable: not run"}
    rows = sms.head(limit)
    out = []
    for _, r in rows.iterrows():
        res = llm.extract(cfg, r["text"])
        f = res["fields"]
        # As in the live SMS path (engine/sms.py): if the regex reads another pair, the message goes to manual entry.
        from engine.sms import regex_bp
        rx = regex_bp(r["text"])
        if res["valid"] and f and rx and (f["sbp"], f["dbp"]) != rx:
            res = {**res, "valid": False, "reason": f"regex read {rx[0]}/{rx[1]}, LLM read {f['sbp']}/{f['dbp']}"}
        gold_sym = sorted(loads(r["symptoms"], []))
        row = {"lang": r["lang"], "messy": r["messy"], "valid": res["valid"], "reason": res["reason"],
               "gold_sym": gold_sym, "got_sym": sorted(f["symptoms"]) if f else None,
               "gold_bp": (r["sbp"], r["dbp"]), "got_bp": (f["sbp"], f["dbp"]) if f else None}
        if f:
            row.update(sbp_ok=_same(r["sbp"], f["sbp"]), dbp_ok=_same(r["dbp"], f["dbp"]),
                       meds_ok=_same(r["meds_taken"], f["meds_taken"]), sym_ok=sorted(f["symptoms"]) == gold_sym)
            row["all_ok"] = row["sbp_ok"] and row["dbp_ok"] and row["meds_ok"] and row["sym_ok"]
        out.append(row)
    df = pd.DataFrame(out)
    ok = df[df["valid"]]

    def acc(col, d=ok):
        return round(float(d[col].mean()), 3) if len(d) else None

    messy = df[df["messy"] == 1]
    # A wrong number that passed the checks is the dangerous case.
    wrong_numbers_accepted = int(((ok["sbp_ok"] == False) | (ok["dbp_ok"] == False)).sum()) if len(ok) else 0  # noqa: E712
    return {
        "model": llm.pick_model(cfg), "n": len(df),
        "accepted": int(df["valid"].sum()), "rejected_rate": round(float(1 - df["valid"].mean()), 3),
        "rejected_rate_messy": round(float(1 - messy["valid"].mean()), 3) if len(messy) else None,
        "field_accuracy_when_accepted": {"sbp": acc("sbp_ok"), "dbp": acc("dbp_ok"), "meds_taken": acc("meds_ok"),
                                         "symptoms": acc("sym_ok"), "all_fields": acc("all_ok")},
        "all_fields_by_language": {lang: acc("all_ok", g) for lang, g in ok.groupby("lang")},
        "wrong_bp_numbers_accepted": wrong_numbers_accepted,
        "top_rejection_reasons": df[~df["valid"]]["reason"].str.slice(0, 60).value_counts().head(3).to_dict(),
        "confusion": _extract_confusion(ok),
    }


def _extract_confusion(ok):
    """Accepted extractions only. Symptoms: per code, missed (false negative) and invented (false positive).
    Readings: a reading missed when the SMS had one, a reading given when it had none, a wrong number."""
    sym = {}
    bp = {"right": 0, "missed_reading": 0, "reading_not_in_sms": 0, "wrong_number": 0}
    for _, r in ok.iterrows():
        gold, got = set(r["gold_sym"]), set(r["got_sym"] or [])
        for code in gold | got:
            c = sym.setdefault(code, {"true_positive": 0, "false_negative": 0, "false_positive": 0})
            c["true_positive" if code in gold and code in got else "false_negative" if code in gold else "false_positive"] += 1
        has_gold = not pd.isna(r["gold_bp"][0])
        has_got = r["got_bp"] is not None and r["got_bp"][0] is not None
        if has_gold and not has_got:
            bp["missed_reading"] += 1
        elif has_got and not has_gold:
            bp["reading_not_in_sms"] += 1
        elif has_gold and (float(r["gold_bp"][0]), float(r["gold_bp"][1])) != (float(r["got_bp"][0]), float(r["got_bp"][1] or 0)):
            bp["wrong_number"] += 1
        else:
            bp["right"] += 1
    return {"symptoms": dict(sorted(sym.items())), "readings": bp}


def eval_decide(con, cfg, labels_dir, ways=None):
    """The decider two ways on decisions_labeled.csv: rules only, and rules plus Julia-1 behind the guardrail.
    Agreement with gold and with the physician column, separately for text-modifier rows and plain rows."""
    import copy
    dec = pd.read_csv(os.path.join(labels_dir, "decisions_labeled.csv"))
    rules_cfg = copy.deepcopy(cfg)
    rules_cfg["julia"]["decide"] = False
    if ways is None:
        ways = {"rules_only": rules_cfg}
        if cfg["julia"].get("decide_mode") == "table":
            ways["rules_plus_table"] = cfg  # the decider in use (PLAN.md 5b): codes + table
        elif models.julia(cfg) is not None and models.julia_on(cfg, "decide"):
            ways["rules_plus_julia"] = cfg
    phys_path = os.path.join(labels_dir, "physician_sample.csv")
    ph = pd.read_csv(phys_path).dropna(subset=["physician_action"]) if os.path.exists(phys_path) else pd.DataFrame()
    out = {"n": len(dec), "n_text_modifier": int((dec["text_modifier"] != "none").sum())}
    for name, c in ways.items():
        res = [decide.suggest(con, c, int(r["patient_id"]), r["date"]) for _, r in dec.iterrows()]
        dec[name] = [x["suggestion"] for x in res]
        dec[name + "_moved"] = [x["moved"] for x in res]
        part = {}
        for label, rows in (("text_modifier_rows", dec[dec["text_modifier"] != "none"]),
                            ("plain_rows", dec[dec["text_modifier"] == "none"])):
            part[label] = {"n": len(rows),
                           "agreement_with_gold": round(float((rows[name] == rows["gold_action"]).mean()), 3),
                           "not_sure": round(float((rows[name] == "not_sure").mean()), 3),
                           "moved": int(rows[name + "_moved"].sum())}
        part["emergencies_caught"] = (f"{int(((dec['gold_action'] == 'emergency_now') & (dec[name] == 'emergency_now')).sum())}"
                                      f" of {int((dec['gold_action'] == 'emergency_now').sum())}")
        part["model_version"] = decide.model_version(con, c)
        if len(ph):
            mm = ph.merge(dec[["encounter_id", name, "text_modifier"]], on="encounter_id")
            part["agreement_with_physician"] = round(float((mm[name] == mm["physician_action"]).mean()), 3)
        else:
            part["agreement_with_physician"] = "physician column not filled yet"
        part["confusion"] = _decider_confusion(cfg, dec["gold_action"], dec[name])
        out[name] = part
    return out


def _decider_confusion(cfg, gold, pred):
    """Gold by suggestion counts, and the mistakes split: too low (false negative, the dangerous one) and
    too high (false positive, a cost in hours). not_sure is counted on its own."""
    from engine.common import action_rank
    actions = cfg["actions"]
    matrix = {g: {p: int(((gold == g) & (pred == p)).sum()) for p in actions} for g in actions if (gold == g).any()}
    low = high = unsure = 0
    for g, p in zip(gold, pred):
        if p == "not_sure":
            unsure += 1
        elif action_rank(cfg, p) < action_rank(cfg, g):
            low += 1
        elif action_rank(cfg, p) > action_rank(cfg, g):
            high += 1
    return {"matrix_gold_by_suggestion": matrix, "false_negative_too_low": low, "false_positive_too_high": high,
            "not_sure": unsure}


def eval_lead_time(con, cfg):
    """The headline number for the 4 October focus (PLAN.md step 2b): for every active synthetic patient whose
    readings cross 140/90 or 160/100 (all baseline readings below it), the days between the first trend or
    borderline flag and the crossing day; the share flagged before the crossing and the median lead time in
    weeks; and the same for a threshold-only configuration (threshold flags, or one reading at or above the goal)."""
    import copy
    import statistics
    from engine import signals
    from engine.common import bp_series, days_between
    only = copy.deepcopy(cfg)
    only["trend"]["rules"] = []
    only.pop("borderline", None)
    goal = cfg["bp_goal"]
    out = {}
    for ts, td in ((goal["sbp_lt"], goal["dbp_lt"]), (cfg["thresholds"][0]["sbp_gte"], cfg["thresholds"][0]["dbp_gte"])):
        rows = {"tool": [], "threshold_only": []}
        n, excluded = 0, 0
        for (pid,) in con.execute("SELECT id FROM patients WHERE status = 'active'").fetchall():
            rs = bp_series(con, pid)
            over = lambda r: r["sbp"] >= ts or (r["dbp"] is not None and r["dbp"] >= td)  # noqa: E731
            base = rs[:cfg["baseline_readings"]]
            if len(rs) <= len(base):
                continue
            if any(over(r) for r in base):
                excluded += 1
                continue
            cross = next((r for r in rs[len(base):] if over(r)), None)
            if cross is None:
                continue
            n += 1
            day = cross["date"][:10]
            earlier = sorted({r["date"][:10] for r in rs if r["date"][:10] < day})
            first = {"tool": None, "threshold_only": None}
            for d in earlier:
                if first["tool"] is None and any(f["type"] in ("trend", "borderline")
                                                 for f in signals.compute(con, cfg, pid, d)["flags"]):
                    first["tool"] = d
                if first["threshold_only"] is None:
                    latest = [r for r in rs if r["date"][:10] <= d][-1]
                    if signals.compute(con, only, pid, d)["flags"] and any(f["type"] == "threshold" for f in
                                                                            signals.compute(con, only, pid, d)["flags"]) \
                            or latest["sbp"] >= goal["sbp_lt"] or (latest["dbp"] or 0) >= goal["dbp_lt"]:
                        first["threshold_only"] = d
            for k in rows:
                rows[k].append(days_between(first[k], day) if first[k] else None)
        res = {"patients_crossing": n, "excluded_started_above": excluded}
        for k, v in rows.items():
            lead = [x for x in v if x is not None]
            res[k] = {"flagged_before": len(lead), "share_flagged_before": round(len(lead) / n, 2) if n else None,
                      "median_lead_weeks": round(statistics.median(lead) / 7, 1) if lead else None}
        out[f"{ts}/{td}"] = res
    return out


def eval_rules_confusion(con, cfg, labels_dir):
    """Per rule: a flag at an encounter against the synthetic 30-day deterioration label at that encounter."""
    from engine import errors, signals
    dec = pd.read_csv(os.path.join(labels_dir, "decisions_labeled.csv"))
    per = {}
    any_rule = {"true_positive": 0, "false_positive": 0, "false_negative": 0, "true_negative": 0}
    rows = []
    for _, r in dec.iterrows():
        fl = signals.compute(con, cfg, int(r["patient_id"]), r["date"])["flags"]
        rows.append(({errors.rule_id(f) for f in fl}, bool(r["deteriorates_30d"])))
    rules = sorted(set().union(*(x[0] for x in rows)))
    for rid in rules:
        c = {"true_positive": 0, "false_positive": 0, "false_negative": 0, "true_negative": 0}
        for fired, det in rows:
            hit = rid in fired
            c["true_positive" if hit and det else "false_positive" if hit else "false_negative" if det else "true_negative"] += 1
        per[rid] = c
    for fired, det in rows:
        hit = bool(fired)
        any_rule["true_positive" if hit and det else "false_positive" if hit else "false_negative" if det else "true_negative"] += 1
    return {"label": "deteriorates within 30 days (synthetic)", "any_rule": any_rule, "per_rule": per}


def to_markdown(r):
    title = "# Independent test" if r.get("independent") else "# Evaluation"
    L = [title, "", f"Generated by engine/evaluate.py on {r['date']}. Synthetic data only (seed {r['seed']}).", ""]
    if r.get("independent"):
        ind = r["independent"]
        L += [f"**Independent test.** Every model that learns was trained on the development set "
              f"({ind['train_db']}, {ind['train_labels']}, seed {ind['train_seed']}) and scored on a held-out set made "
              f"separately ({ind['test_db']}, {ind['test_labels']}, seed {r['seed']}): different patients, readings and "
              "SMS numbers, SMS templates never seen in training, and text-modifier notes written with phrasings the "
              "development set never uses. The peer-trajectory index is built from development patients and scored on "
              "held-out patients. Rules and Julia-1 do not learn from data, so their rows simply run on the held-out set. "
              "Both sets come from the same simulator, so this tests generalisation to new cases, not to real patients.", ""]
    L += [
         "**Read this first.** Every label here is synthetic. The SMS labels come from templates, so they are known by "
         "construction. The action labels come from the config rules, so agreement only shows that the pipeline follows "
         "the rules. The physician sample is the only clinical check. None of this is a clinical validation.", "",
         "| Model | Test | Result |", "|---|---|---|"]
    i = r["intent"]

    def by_lang(x):
        b = x["by_language"]
        return f"{x['accuracy']} (sw {b.get('sw')}, fr {b.get('fr')}, en {b.get('en')})"

    L.append(f"| SMS intent, TF-IDF | accuracy on {i['templates_unseen']} message templates never seen in training "
             f"({i['n']} SMS) | {by_lang(i['tfidf'])} |")
    if "julia" in i:
        L.append(f"| SMS intent, Julia-1 zero-shot | same unseen templates | {by_lang(i['julia'])} |")
        mix = i["in_use_mix"]
        L.append(f"| SMS intent, **in use** (TF-IDF, Julia-1 when TF-IDF is unsure: {mix['julia_used_on']} SMS) | "
                 f"same unseen templates | {by_lang(mix)} |")
    e = r["extract"]
    if "note" in e:
        L.append(f"| LLM extraction | field accuracy | {e['note']} |")
    else:
        fa = e["field_accuracy_when_accepted"]
        L.append(f"| LLM extraction ({e['model']}) | all fields right, of accepted ({e['accepted']} of {e['n']}) | "
                 f"{fa['all_fields']} (sbp {fa['sbp']}, dbp {fa['dbp']}, meds {fa['meds_taken']}, symptoms {fa['symptoms']}) |")
        L.append(f"| LLM extraction | sent to manual entry: all / messy | {e['rejected_rate']} / {e['rejected_rate_messy']} |")
        L.append(f"| LLM extraction | wrong BP number that passed all checks | {e['wrong_bp_numbers_accepted']} |")
    d = r["decide"]
    for name, label in (("rules_only", "Decider, rules only"), ("rules_plus_table", "Decider, rules + context codes + table (in use)"),
                        ("rules_plus_julia", "Decider, rules + Julia-1 (guardrail)")):
        if name not in d:
            continue
        x = d[name]
        tm, pl = x["text_modifier_rows"], x["plain_rows"]
        L.append(f"| {label} | agreement with gold: text-modifier rows ({tm['n']}) / plain rows ({pl['n']}) | "
                 f"{tm['agreement_with_gold']} / {pl['agreement_with_gold']} (not_sure {tm['not_sure']} / {pl['not_sure']}) |")
        L.append(f"| {label} | emergencies caught / agreement with physician | {x['emergencies_caught']} / "
                 f"{x['agreement_with_physician']} |")
    if r.get("quiz_reply"):
        qr = r["quiz_reply"]
        L.append(f"| Quiz free-text replies ({qr['backend']}) | rephrased option mapped right / nonsense reply unclear, "
                 f"{qr['n']} replies | {qr['mapped_right']} / {qr['unclear_right']} |")
    t = r["retrain"]
    L.append(f"| Next-action model (TF-IDF + LR) | held-out accuracy / macro-F1, {t['decider']['n_test']} rows | "
             f"{t['decider']['accuracy']} / {t['decider']['macro_f1']} |")
    L.append(f"| 30-day deterioration (LR on risk components) | AUC, held-out patients | "
             f"{t['deterioration'].get('auc_heldout_patients')} |")
    k = r["trajectory"]
    if "note" in k:
        L.append(f"| Peer trajectories (kNN) | leave one patient out | {k['note']} |")
    else:
        L.append(f"| Peer trajectories (kNN, min_events {k['min_events_used']}) | "
                 + (f"AUC on held-out patients, index from development patients ({k['windows']} windows, "
                    f"{k.get('training_events')} training events) | " if r.get("independent") else
                    f"AUC, leave one patient out ({k['windows']} windows, {k['events']} events) | ")
                 + f"{k['auc_leave_one_patient_out']} |")
        L.append(f"| Peer trajectories | share flagged: patients with / without an event | "
                 f"{k['share_flagged_event_patients']} / {k['share_flagged_no_event_patients']} |")
    L += _lead_markdown(r)
    L += _confusion_markdown(r)
    L += ["", "## Limits", "",
          "- The synthetic SMS come from about 30 templates per language. Real patient messages will be messier, and "
          "accuracy will be lower. Kirundi SMS are not tested at all.",
          "- Intent is scored on message templates never seen in training. Julia-1 is zero-shot; config.julia "
          "says which jobs it does.",
          "- Plain-row gold labels are the config rules, so rules-only agreement there is 1 by construction. The "
          "number that matters for Julia-1 is the gain over rules only on text-modifier rows, against the physician "
          "column once it is filled. Text-modifier gold comes from how the synthetic cases were written.",
          "- The peer-trajectory history includes 30 synthetic former patients (18 with an event), so the events table "
          "reaches config.trajectory.min_events.",
          "- The deterioration and trajectory labels come from a synthetic process with only "
          f"{k.get('events', 'a few')} events. They show that the mechanism works, not that it predicts anything.",
          "- In deployment the peer-trajectory flag stays off until the local events table reaches config.trajectory.min_events.",
          "", "## Raw results", "", "```json", json.dumps(r, indent=2, default=str), "```", ""]
    return "\n".join(L)


def _lead_markdown(r):
    lt = r.get("lead_time")
    if not lt:
        return []
    L = ["", "## Lead time: the headline number", "",
         "Patients whose readings cross the line during the six months (all baseline readings below it). "
         "\"Tool\" = first trend or borderline flag before the crossing; \"threshold only\" = a threshold flag or a "
         "single reading at or above the goal, today's practice. For the 140/90 line, threshold-only cannot flag before "
         "the crossing by construction.", "",
         "| Line | Patients crossing | Tool: flagged before / median lead | Threshold only: flagged before / median lead |",
         "|---|---|---|---|"]
    for line, x in lt.items():
        t, o = x["tool"], x["threshold_only"]
        L.append(f"| {line} | {x['patients_crossing']} (excluded, started above: {x['excluded_started_above']}) | "
                 f"{t['share_flagged_before']} / {t['median_lead_weeks']} weeks | {o['share_flagged_before']} / "
                 f"{o['median_lead_weeks']} weeks |")
    return L


def _confusion_markdown(r):
    """False negatives listed apart from false positives: a missed stroke and an unneeded visit are not the
    same mistake."""
    L = ["", "## Confusion matrices", "",
         "False negatives (missed) and false positives (over-called) are listed separately.", ""]
    rc = r.get("rules_confusion")
    if rc:
        L += [f"**Rules, per rule** (flag at an encounter against: {rc['label']})", "",
              "| Rule | True positive | False negative (missed) | False positive (over-called) | True negative |", "|---|---|---|---|---|"]
        for rid, c in [("any rule", rc["any_rule"])] + list(rc["per_rule"].items()):
            L.append(f"| {rid} | {c['true_positive']} | {c['false_negative']} | {c['false_positive']} | {c['true_negative']} |")
        L.append("")
    for name, label in (("rules_only", "Decider, rules only"), ("rules_plus_table", "Decider, rules + codes + table"),
                        ("rules_plus_julia", "Decider, rules + Julia-1")):
        x = r["decide"].get(name)
        if not x or "confusion" not in x:
            continue
        c = x["confusion"]
        acts = list(next(iter(c["matrix_gold_by_suggestion"].values())).keys())
        L += [f"**{label}**: too low (false negative) {c['false_negative_too_low']}, too high (false positive) "
              f"{c['false_positive_too_high']}, not sure {c['not_sure']}", "",
              "| Gold \\ suggestion | " + " | ".join(acts) + " |", "|---|" + "---|" * len(acts)]
        for g, row in c["matrix_gold_by_suggestion"].items():
            L.append(f"| {g} | " + " | ".join(str(row[a]) for a in acts) + " |")
        L.append("")
    e = r.get("extract", {})
    if "confusion" in e:
        b = e["confusion"]["readings"]
        L += ["**LLM extraction** (accepted messages)", "",
              f"Readings: right {b['right']}, missed reading (false negative) {b['missed_reading']}, reading not in the SMS "
              f"(false positive) {b['reading_not_in_sms']}, wrong number {b['wrong_number']}.", "",
              "| Symptom | Found | Missed (false negative) | Invented (false positive) |", "|---|---|---|---|"]
        for code, c in e["confusion"]["symptoms"].items():
            L.append(f"| {code} | {c['true_positive']} | {c['false_negative']} | {c['false_positive']} |")
        L.append("")
    k = r.get("trajectory", {})
    if "confusion" in k:
        c = k["confusion"]
        L += [f"**Peer trajectories** (windows, flag at share >= threshold): true positive {c['true_positive']}, "
              f"missed (false negative) {c['false_negative']}, over-called (false positive) {c['false_positive']}, "
              f"true negative {c['true_negative']}.", ""]
    return L


def check_labels_match(con, labels):
    """Labels are per encounter date: a database built on another day (another end date) does not match them."""
    path = os.path.join(labels, "labels_meta.json")
    if not os.path.exists(path):
        return
    want = json.load(open(path))
    have = {"synthetic_end_date": get_meta(con, "synthetic_end_date"), "seed": get_meta(con, "seed")}
    if have["synthetic_end_date"] != want["synthetic_end_date"]:
        raise SystemExit(f"The labels in {labels} were made from a database ending {want['synthetic_end_date']}, but this "
                         f"database ends {have['synthetic_end_date']}. Rebuild it with --end-date {want['synthetic_end_date']}.")
# ---------- PLAN.md 5b: Design B, "facts first" ----------

def eval_codes(cfg, labels_dir):
    """Context-code precision and recall on the text-modifier cases (gold codes), plus neutral texts (no codes),
    and how many accepted codes had a quote that is not in the text (must be 0)."""
    from engine.llm import _norm
    df = pd.read_csv(os.path.join(labels_dir, "codes_labeled.csv"))
    tp = fp = fn = bad_quote = rejected = 0
    per = {}
    for _, r in df.iterrows():
        gold = {c["code"] for c in json.loads(r["gold_codes"])}
        res = llm.extract(cfg, r["text"])
        if not res["valid"]:
            rejected += 1
            got = set()
        else:
            got = {c["code"] for c in res["fields"]["context_codes"]}
            bad_quote += sum(_norm(c["quote"]) not in _norm(r["text"]) for c in res["fields"]["context_codes"])
        for code in gold | got:
            p = per.setdefault(code, {"tp": 0, "fp": 0, "fn": 0})
            k = "tp" if code in gold and code in got else "fn" if code in gold else "fp"
            p[k] += 1
        tp += len(gold & got); fp += len(got - gold); fn += len(gold - got)
    return {"texts": len(df), "with_gold_codes": int((df["gold_codes"] != "[]").sum()), "rejected": rejected,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "true_positive": tp, "false_positive": fp, "false_negative": fn, "accepted_codes_with_quote_not_in_text": bad_quote,
            "per_code": dict(sorted(per.items()))}


def gate_report(codes, dec):
    """The merge gate from PLAN.md 5b, line by line."""
    jb, tb, ro, da = (dec.get(k) for k in ("codes_plus_julia", "codes_plus_table", "rules_only", "design_a"))
    caught = lambda x: int(x["emergencies_caught"].split(" of ")[0]) if x else None  # noqa: E731
    lines = []
    ok_codes = (codes["precision"] or 0) >= 0.9 and (codes["recall"] or 0) >= 0.9 and codes["accepted_codes_with_quote_not_in_text"] == 0
    lines.append(("Context codes: precision and recall >= 0.9, no accepted quote outside the text",
                  f"precision {codes['precision']}, recall {codes['recall']}, quotes outside the text "
                  f"{codes['accepted_codes_with_quote_not_in_text']}", ok_codes))
    pl = jb["plain_rows"] if jb else None
    ok_plain = bool(pl) and pl["agreement_with_gold"] >= 0.95 and pl["not_sure"] <= 0.05
    lines.append(("Plain rows: agreement with the rule default >= 0.95, not_sure <= 0.05 (codes + Julia-1)",
                  f"agreement {pl['agreement_with_gold'] if pl else None}, not_sure {pl['not_sure'] if pl else None}", ok_plain))
    tm = lambda x: x["text_modifier_rows"]["agreement_with_gold"] if x else None  # noqa: E731
    ok_tm = bool(jb and tb and ro) and tm(jb) >= tm(tb) and tm(jb) > tm(ro) and tm(tb) > tm(ro)
    lines.append(("Text-modifier rows: codes + Julia-1 >= codes + table, both above rules only",
                  f"codes + Julia-1 {tm(jb)}, codes + table {tm(tb)}, rules only {tm(ro)}", ok_tm))
    ok_em = bool(jb and da) and caught(jb) >= caught(da)
    lines.append(("Emergencies caught: not fewer than main (Design A)",
                  f"codes + Julia-1 {jb['emergencies_caught'] if jb else None}, Design A {da['emergencies_caught'] if da else None}", ok_em))
    lines.append(("Physician sample: agreement on text-modifier rows not lower than main", "physician column not filled yet", None))
    if ok_plain and ok_em and ok_codes and ok_tm:
        verdict = "All pass with Julia-1 ahead: merge, decide_mode facts."
    elif ok_plain and ok_em and ok_codes and bool(jb and tb) and tm(tb) >= tm(jb) and tm(tb) > tm(ro):
        verdict = "All pass except that the table ties or beats Julia-1: merge with the table as the decider."
    elif not ok_plain or not ok_em:
        verdict = "Plain rows or emergencies fail: stay on main, keep the branch, report the numbers as a tried alternative."
    else:
        verdict = "Not all lines pass: do not merge; see the lines above."
    return {"lines": [{"line": a, "measured": m, "pass": p} for a, m, p in lines], "verdict": verdict}


def main_layer3(args, con, cfg, labels):
    """PLAN.md 5b evaluation on the branch: context codes, the decider four ways, the gate."""
    import copy
    from engine import context
    print("1/3 context codes for every note and message (one LLM call per distinct text) ...")
    filled = context.backfill(con, cfg)
    print("   ", filled)
    print("2/3 context-code precision and recall ...")
    codes = eval_codes(cfg, labels)
    held = eval_codes(cfg, root_path(args.heldout_labels)) if args.heldout_labels else None
    print("3/3 the decider four ways ...")
    ways = {}
    for name, mode, on in (("rules_only", "actions", False), ("codes_plus_table", "table", True),
                           ("codes_plus_julia", "facts", True), ("design_a", "actions", True)):
        c = copy.deepcopy(cfg)
        c["julia"]["decide"], c["julia"]["decide_mode"] = on, mode
        ways[name] = c
    dec = eval_decide(con, cfg, labels, ways=ways)
    gate = gate_report(codes, dec)
    r = {"date": date.today().isoformat(), "seed": get_meta(con, "seed"), "config_version": cfg["config_version"],
         "backfill": filled, "codes": codes, "codes_heldout": held, "decide": dec, "gate": gate}
    L = ["# Layer 3, Design B (\"facts first\"): evaluation on branch layer3-facts", "",
         f"Generated by engine/evaluate.py --layer3-facts on {r['date']}. Synthetic data only (seed {r['seed']}). "
         "Same labels as main. Not merged: PLAN.md section 5b.", "",
         "## Context codes (Gemma reads the free text)", "",
         f"{codes['texts']} texts ({codes['with_gold_codes']} text-modifier cases with gold codes, the rest neutral). "
         f"Precision {codes['precision']}, recall {codes['recall']} (true positive {codes['true_positive']}, false positive "
         f"{codes['false_positive']}, false negative {codes['false_negative']}); accepted codes whose quote is not in the "
         f"text: {codes['accepted_codes_with_quote_not_in_text']}; texts sent to manual entry: {codes['rejected']}.", "",
         (f"On the held-out phrasings (a separate set the phrase list was not written from): precision "
          f"{held['precision']}, recall {held['recall']} (true positive {held['true_positive']}, false positive "
          f"{held['false_positive']}, false negative {held['false_negative']}). " if held else "") +
         "The phrase list in config.context_lexicon was written by the engine team, who also wrote both synthetic "
         "phrasing sets, so recall here is flattered; real messages are the real test.", "",
         "| Code | Found | Missed | Invented |", "|---|---|---|---|"]
    for code, p in codes["per_code"].items():
        L.append(f"| {code} | {p['tp']} | {p['fn']} | {p['fp']} |")
    L += ["", "## The decider four ways", "",
          "| Decider | Text-modifier rows right | Plain rows right | not_sure (modifier / plain) | Emergencies caught |",
          "|---|---|---|---|---|"]
    for name, label in (("rules_only", "Rules only"), ("codes_plus_table", "Codes + table"),
                        ("codes_plus_julia", "Codes + Julia-1 (Design B)"), ("design_a", "Design A (main)")):
        x = dec[name]
        L.append(f"| {label} | {x['text_modifier_rows']['agreement_with_gold']} | {x['plain_rows']['agreement_with_gold']} | "
                 f"{x['text_modifier_rows']['not_sure']} / {x['plain_rows']['not_sure']} | {x['emergencies_caught']} |")
    L += ["", "## The gate (PLAN.md 5b)", "", "| Line | Measured | Pass |", "|---|---|---|"]
    for g in gate["lines"]:
        L.append(f"| {g['line']} | {g['measured']} | {'yes' if g['pass'] else 'no' if g['pass'] is False else 'pending'} |")
    L += ["", f"**Verdict:** {gate['verdict']}", "", "## Raw results", "", "```json",
          json.dumps({k: v for k, v in r.items()}, indent=2, default=str), "```", ""]
    with open(root_path(args.out), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L).split("## Raw results")[0])
    print(f"Saved {args.out}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate every model on the labeled synthetic sets.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--llm-limit", type=int, default=300, help="how many SMS to send to the LLM")
    parser.add_argument("--traj-min-events", type=int, default=None,
                        help="override config.trajectory.min_events for the trajectory evaluation")
    parser.add_argument("--out", default="docs/evaluation.md")
    parser.add_argument("--train-db", default=None,
                        help="independent test: the development database the trajectory index is built from")
    parser.add_argument("--train-labels", default=None,
                        help="independent test: the development labels the learning models train on; "
                             "--db and --labels are then the held-out set")
    parser.add_argument("--heldout-labels", default=None,
                        help="with --layer3-facts: a labels folder whose codes_labeled.csv holds the held-out phrasings")
    parser.add_argument("--layer3-facts", action="store_true",
                        help="PLAN.md 5b on the branch: context codes, the decider four ways and the merge gate")
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    labels = root_path(args.labels)
    check_labels_match(con, labels)
    if args.layer3_facts:
        return main_layer3(args, con, cfg, labels)
    sms = pd.read_csv(os.path.join(labels, "sms_labeled.csv"))
    independent = bool(args.train_labels)
    if independent:
        check_labels_match(connect(args.train_db), root_path(args.train_labels))
    if independent and not args.train_db:
        parser.error("--train-labels needs --train-db")

    print("1/5 intent ...")
    if independent:
        intent = eval_intent_independent(cfg, pd.read_csv(os.path.join(root_path(args.train_labels), "sms_labeled.csv")), sms)
    else:
        intent = eval_intent(cfg, sms)
    print("2/5 LLM extraction (about 1 to 2 seconds per SMS) ...")
    extract = eval_extract(cfg, sms, args.llm_limit)
    if cfg["julia"].get("decide_mode") in ("table", "facts"):
        from engine import context
        print("   context codes for every note and message (LLM and phrase list, one call per distinct text) ...")
        print("   ", context.backfill(con, cfg))
    print("3/5 decider, rules only and the decider in use (a few minutes) ...")
    dec = eval_decide(con, cfg, labels)
    print("   lead time before 140/90 and 160/100 ...")
    lead = eval_lead_time(con, cfg)
    print("   rules per rule, against the 30-day deterioration label ...")
    rules_conf = eval_rules_confusion(con, cfg, labels)
    print("   Julia-1 on quiz replies ...")
    qr = eval_quiz_reply(cfg)
    print("4/5 retrain (held out, nothing saved) ...")
    if independent:
        rt = eval_retrain_independent(root_path(args.train_labels), labels)
    else:
        rt = retrain.run_review(con, cfg, labels, save=False)
    print("5/5 trajectories ...")
    if independent:
        train_con = connect(args.train_db)
        tj = trajectory.evaluate_external(train_con, con, cfg, None, args.traj_min_events)
    else:
        tj = trajectory.evaluate(con, cfg, None, args.traj_min_events)

    r = {"date": date.today().isoformat(), "seed": get_meta(con, "seed"), "config_version": cfg["config_version"],
         "intent": intent, "extract": extract, "decide": dec, "quiz_reply": qr,
         "retrain": rt, "trajectory": tj, "rules_confusion": rules_conf, "lead_time": lead}
    if independent:
        r["independent"] = {"train_db": args.train_db, "train_labels": args.train_labels, "test_db": args.db,
                            "test_labels": args.labels, "train_seed": get_meta(train_con, "seed")}
    md = to_markdown(r)
    with open(root_path(args.out), "w", encoding="utf-8") as f:
        f.write(md)
    print(md.split("## Limits")[0])
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
