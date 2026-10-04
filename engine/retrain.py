"""Step 11. The monthly review: retrain the small local models. A person runs it; it never runs by itself.

1. Next action: TF-IDF plus logistic regression on the decisions table (state text -> the CHW's final choice,
   so overrides teach the model). When the table is small, data/labels/decisions_labeled.csv is added.
2. Deterioration within 30 days: logistic regression on the risk components in decisions_labeled.csv.
Prints held-out scores, saves the models in --out, and writes the new model version to the meta table.
The next suggestion for any patient then carries the new version.

Usage:
    python engine/retrain.py --db data/chw.db --labels data/labels --out models [--config config/guideline_htn.json]
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import models
from engine.common import connect, get_meta, load_config, loads, now_iso, root_path, set_meta

MIN_TABLE_ROWS = 300  # below this, the labeled synthetic set is added to the decisions table
DET_FEATURES = ["threshold_flag", "trend_flag", "symptom_flag", "missed_meds", "silent_30_days",
                "overdue_followup", "missed_appointment", "low_understanding", "score"]


def decision_data(con, labels_dir):
    """Texts, labels, weights and where they came from."""
    rows = con.execute("SELECT state_json, suggestion, final_choice FROM decisions WHERE final_choice IS NOT NULL").fetchall()
    texts, labels, weights = [], [], []
    overrides = 0
    for r in rows:
        st = loads(r["state_json"], {}) or {}
        if not st.get("text"):
            continue
        texts.append(st["text"])
        labels.append(r["final_choice"])
        weights.append(2.0)  # a real CHW choice counts double
        overrides += r["final_choice"] != r["suggestion"]
    from_table = len(texts)
    from_labels = 0
    path = os.path.join(labels_dir, "decisions_labeled.csv")
    if from_table < MIN_TABLE_ROWS and os.path.exists(path):
        df = pd.read_csv(path)
        texts += df["state"].astype(str).tolist()
        labels += df["gold_action"].tolist()
        weights += [1.0] * len(df)
        from_labels = len(df)
    return texts, labels, np.array(weights), {"from_decisions_table": from_table, "from_labeled_csv": from_labels,
                                              "overrides": overrides}


def train_decider(texts, labels, weights, seed=1, holdout=0.25):
    """Held-out scores, then the model fitted on everything."""
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.model_selection import train_test_split

    y = np.array(labels)
    idx = np.arange(len(texts))
    counts = pd.Series(y).value_counts()
    strat = y if counts.min() >= 2 else None
    tr, te = train_test_split(idx, test_size=holdout, random_state=seed, stratify=strat)
    m = models.train_text_model([texts[i] for i in tr], y[tr])
    m.fit([texts[i] for i in tr], y[tr], logisticregression__sample_weight=weights[tr])
    pred = m.predict([texts[i] for i in te])
    probs = m.predict_proba([texts[i] for i in te]).max(axis=1)
    scores = {"n_train": int(len(tr)), "n_test": int(len(te)),
              "accuracy": round(float(accuracy_score(y[te], pred)), 3),
              "macro_f1": round(float(f1_score(y[te], pred, average="macro", zero_division=0)), 3),
              "mean_top_probability": round(float(probs.mean()), 3)}
    final = models.train_text_model(texts, y)
    final.fit(texts, y, logisticregression__sample_weight=weights)
    return final, scores


def train_deterioration(labels_dir, seed=1):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split

    path = os.path.join(labels_dir, "decisions_labeled.csv")
    if not os.path.exists(path):
        return None, {"note": "decisions_labeled.csv not found"}
    df = pd.read_csv(path).fillna(0)
    X, y = df[DET_FEATURES].values, df["deteriorates_30d"].values
    if len(set(y)) < 2:
        return None, {"note": "only one class in the labels"}
    # Split by patient, so a patient's rows never sit on both sides.
    pats = df["patient_id"].unique()
    tr_p, te_p = train_test_split(pats, test_size=0.3, random_state=seed)
    tr, te = df["patient_id"].isin(tr_p).values, df["patient_id"].isin(te_p).values
    m = LogisticRegression(max_iter=1000, class_weight="balanced").fit(X[tr], y[tr])
    auc = roc_auc_score(y[te], m.predict_proba(X[te])[:, 1]) if len(set(y[te])) > 1 else None
    final = LogisticRegression(max_iter=1000, class_weight="balanced").fit(X, y)
    return final, {"n_train": int(tr.sum()), "n_test": int(te.sum()), "positives": int(y.sum()),
                   "auc_heldout_patients": round(float(auc), 3) if auc is not None else None}


def run_review(con, cfg, labels_dir=None, save=True, seed=1):
    """Train both models, save them, store the new version. Returns the scores."""
    labels_dir = root_path(labels_dir or cfg.get("paths", {}).get("labels_dir", "data/labels"))
    texts, labels, weights, source = decision_data(con, labels_dir)
    if len(set(labels)) < 2:
        return {"error": "need at least two different actions to train"}
    decider, dscores = train_decider(texts, labels, weights, seed)
    det, tscores = train_deterioration(labels_dir, seed)
    n = int(get_meta(con, "decider_version_n", "0")) + 1
    version = f"lr-v{n}"
    quiz_answers = con.execute("SELECT COUNT(*) FROM quiz_results WHERE answer IS NOT NULL").fetchone()[0]
    out = {"model_version": version, "trained_at": now_iso(), "decider": dscores, "deterioration": tscores,
           "learned_from": {**source, "quiz_answers": quiz_answers}, "saved": save,
           "previous_version": get_meta(con, "decider_version")}
    if save:
        models.save_pickle(cfg, f"decider_{version}.pkl", decider)
        if det is not None:
            models.save_pickle(cfg, f"deterioration_{version}.pkl", det)
        set_meta(con, "decider_version", version)
        set_meta(con, "decider_version_n", n)
        set_meta(con, f"review:{version}", json.dumps(out))
        con.commit()
    return out


def main():
    parser = argparse.ArgumentParser(description="Monthly review: retrain the local models.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--out", required=True, help="folder for the model files, for example models")
    parser.add_argument("--config", default="config/guideline_htn.json")
    parser.add_argument("--dry-run", action="store_true", help="print scores, save nothing")
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    cfg.setdefault("paths", {})["models_dir"] = args.out
    out = run_review(con, cfg, args.labels, save=not args.dry_run)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
