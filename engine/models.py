"""The small models, loaded once and kept in memory.

- Julia-1 (SupersonicLabs/Julia-1): picks one option from a fixed list, given a state.
  Used for SMS intent, the next-action suggestion, and mapping free-text quiz replies.
- Fallback when Julia-1 is not installed (PLAN.md section 9): TF-IDF plus logistic regression,
  trained on the labeled sets in data/labels and on the decisions table by engine/retrain.py.
"""

import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import root_path

_julia = {"engine": None, "tried": False, "error": None}
_pickles = {}


# ---------- Julia-1 ----------

def julia(cfg):
    """Load Julia-1 once. Returns the engine, or None if it is not installed."""
    if _julia["tried"]:
        return _julia["engine"]
    _julia["tried"] = True
    path = root_path(cfg.get("paths", {}).get("julia_dir", "models/Julia-1"))
    if not os.path.isdir(path):
        _julia["error"] = f"Julia-1 not found in {path}"
        return None
    try:
        from julia import load_model  # installed with: pip install -e ./models/Julia-1
        _julia["engine"] = load_model(path, device="cpu", strict_encoding=True,
                                      max_length=8192, head_length=512)
    except Exception as e:  # not installed, or failed to load: use the fallback
        _julia["error"] = f"Julia-1 failed to load: {e}"
        _julia["engine"] = None
    return _julia["engine"]


def julia_status(cfg):
    eng = julia(cfg)
    used = []
    for job in ("intent", "decide", "quiz_reply"):
        mode = cfg.get("julia", {}).get(job, False)
        if mode:
            used.append(job if mode is True else f"{job} ({mode})")
    return {"available": eng is not None, "error": _julia["error"], "used_for": used}


def julia_on(cfg, job):
    """True when the config gives this job (intent, decide, quiz_reply) to Julia-1, in any mode."""
    return bool(cfg.get("julia", {}).get(job, False))


def julia_choose(cfg, state, question, options):
    """Ask Julia-1 one choice question.

    options: dict of option id -> short description (2 to 20 options).
    Returns (choice_id, {id: probability}) or None when Julia-1 is not available.
    """
    eng = julia(cfg)
    if eng is None:
        return None
    result = eng.predict(
        state=state,
        questions={"q": {"type": "choice", "instructions": question, "criteria": options}},
    )
    answer = result["answers"]["q"]
    probs = {k: float(v) for k, v in answer["probabilities"].items()}
    return answer["choice"], probs


# ---------- TF-IDF plus logistic regression ----------

def train_text_model(texts, labels, char_ngrams=False):
    """Fit TF-IDF plus logistic regression. Char n-grams cope better with typos and mixed languages."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline

    if char_ngrams:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1, sublinear_tf=True)
    else:
        vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
    model = make_pipeline(vec, LogisticRegression(max_iter=2000, C=5.0))
    model.fit(texts, labels)
    return model


def predict_proba(model, text):
    """Probabilities per class for one text, as a dict."""
    probs = model.predict_proba([text])[0]
    return {c: float(p) for c, p in zip(model.classes_, probs)}


def save_pickle(cfg, name, obj):
    folder = root_path(cfg.get("paths", {}).get("models_dir", "models"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        pickle.dump(obj, f)
    _pickles[path] = obj
    return path


def load_pickle(cfg, name):
    """Load a saved model once; None when the file does not exist."""
    folder = root_path(cfg.get("paths", {}).get("models_dir", "models"))
    path = os.path.join(folder, name)
    if path in _pickles:
        return _pickles[path]
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        _pickles[path] = pickle.load(f)
    return _pickles[path]


# ---------- SMS intent ----------

INTENTS = {
    "bp_report": "The message gives blood pressure numbers, such as 150/95 or 150 na 95, possibly with medicine or symptoms",
    "symptom": "The message describes feeling unwell or a symptom, with no blood pressure numbers",
    "meds_question": "The message asks a question about medicine, tablets, side effects or a refill",
    "other": "A greeting, thanks or blessing, with no health information",
}
INTENT_QUESTION = "What kind of message did the patient send?"


def intent_model(cfg):
    """The fallback intent model: load models/intent_lr.pkl, or train it from data/labels/sms_labeled.csv."""
    model = load_pickle(cfg, "intent_lr.pkl")
    if model is not None:
        return model
    labels = os.path.join(root_path(cfg.get("paths", {}).get("labels_dir", "data/labels")), "sms_labeled.csv")
    if not os.path.exists(labels):
        return None
    import pandas as pd
    df = pd.read_csv(labels)
    model = train_text_model(df["text"].astype(str).tolist(), df["intent"].tolist(), char_ngrams=True)
    save_pickle(cfg, "intent_lr.pkl", model)
    return model


def classify_intent(cfg, text, has_numbers=False):
    """Name the intent of one SMS. Returns (intent, probabilities, backend).
    config.julia.intent: true = Julia-1 first; "fallback_only" = the TF-IDF model first, and Julia-1 reads the
    message only when the TF-IDF top probability is under config.julia.intent_fallback_below; false = never."""
    mode = cfg.get("julia", {}).get("intent", False)
    if mode is True:
        out = julia_choose(cfg, text, INTENT_QUESTION, INTENTS)
        if out:
            return out[0], out[1], "julia-1"
    model = intent_model(cfg)
    if model is not None:
        probs = predict_proba(model, text)
        top = max(probs, key=probs.get)
        if mode == "fallback_only" and probs[top] < cfg["julia"].get("intent_fallback_below", 0.7):
            out = julia_choose(cfg, text, INTENT_QUESTION, INTENTS)
            if out:
                return out[0], out[1], "julia-1 (tfidf unsure)"
        return top, probs, "tfidf-lr"
    # Last resort with no model at all: numbers mean a report.
    intent = "bp_report" if has_numbers else "other"
    return intent, {intent: 1.0}, "keyword"
