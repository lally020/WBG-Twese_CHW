"""Patient-facing text, written by people, read from content/i18n and content/quiz.
The engine never writes patient-facing text itself; it only fills placeholders like {date}."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import ROOT

CONTENT = os.environ.get("TWESE_CONTENT", os.path.join(ROOT, "content"))
_cache = {}


def _load(path):
    if path not in _cache:
        with open(path, encoding="utf-8") as f:
            _cache[path] = json.load(f)
    return _cache[path]


def language_file(lang):
    path = os.path.join(CONTENT, "i18n", f"{lang}.json")
    return _load(path) if os.path.exists(path) else None


def languages():
    """The four language codes with their verified flags, for the frontend."""
    out = []
    for lang in ("en", "fr", "sw", "rn"):
        f = language_file(lang)
        if f:
            out.append({"lang": lang, "name": f.get("language_name", lang), "verified": f.get("verified", False),
                        "note": f.get("note", "")})
    return out


def sms_text(key, lang, **values):
    """One SMS template in the patient's language (English when missing). Returns (text, lang, verified)."""
    f = language_file(lang)
    if not f or key not in f.get("strings", {}):
        f, lang = language_file("en"), "en"
    text = f["strings"][key].format(**values)
    return text, lang, f.get("verified", False)


def quiz_bank():
    return _load(os.path.join(CONTENT, "quiz", "patient_htn.json"))


def quiz_question(question_id):
    for q in quiz_bank()["questions"]:
        if q["id"] == question_id:
            return q
    return None
