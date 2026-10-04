"""Layer 4: the local LLM, through Ollama on localhost. Two jobs only.

Job 1, extract: turn one SMS or note into fields (sbp, dbp, meds_taken, symptoms, free_text_rest),
       and the medications variant for a doctor's recommendation.
Job 2, summary: a three-line French "what changed since last contact", from structured fields only.

Every output passes four checks before it touches the record: valid JSON, fields match the schema,
every number appears in the source text, symptom codes and drug names come from the config lists.
A failed check means manual entry, never a guess. Every call is written to llm_log.
The LLM never chooses an action, never writes advice, never writes text that goes to a patient.

Usage:
    python engine/llm.py --config config/guideline_htn.json --text "Presha yangu ni 150/95"
    python engine/llm.py --config config/guideline_htn.json --medications "Amlodipine 5 mg once daily"
    python engine/llm.py --db data/chw.db --config config/guideline_htn.json --patient 1
"""

import argparse
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import connect, dumps, load_config, loads, now_iso

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
_state = {"model": None}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "sbp": {"type": ["integer", "null"]},
        "dbp": {"type": ["integer", "null"]},
        "meds_taken": {"type": ["string", "null"], "enum": ["yes", "no", "partial", None]},
        "symptoms": {"type": "array", "items": {"type": "string"}},
        "context_codes": {
            "type": "array",
            "items": {"type": "object", "properties": {"code": {"type": "string"}, "quote": {"type": "string"}},
                      "required": ["code", "quote"]},
        },
        "free_text_rest": {"type": "string"},
    },
    "required": ["sbp", "dbp", "meds_taken", "symptoms", "context_codes", "free_text_rest"],
}

MEDS_SCHEMA = {
    "type": "object",
    "properties": {
        "medications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "dose_text": {"type": "string"}},
                "required": ["name", "dose_text"],
            },
        }
    },
    "required": ["medications"],
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"],
}


# ---------- Ollama ----------

def reachable():
    try:
        return requests.get(f"{OLLAMA}/api/tags", timeout=2).ok
    except requests.RequestException:
        return False


def installed_models():
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=2)
        return [m["name"] for m in r.json().get("models", [])]
    except (requests.RequestException, ValueError):
        return []


def free_ram_gb():
    import psutil
    return psutil.virtual_memory().available / 1e9


def pick_model(cfg):
    """The primary model when there is enough free RAM, else the fallback; only models that are pulled."""
    if _state["model"]:
        return _state["model"]
    llm = cfg["llm"]
    have = installed_models()
    want = llm["model"] if free_ram_gb() >= llm["min_free_ram_gb"] else llm["fallback_model"]
    order = [want] + [m for m in (llm["model"], llm["fallback_model"]) if m != want]
    for m in order:
        if m in have or f"{m}:latest" in have:
            _state["model"] = m
            return m
    return None


def status(cfg):
    ok = reachable()
    return {"ollama_reachable": ok, "model": pick_model(cfg) if ok else None,
            "free_ram_gb": round(free_ram_gb(), 1)}


def _chat(cfg, system, user, schema):
    """One call to Ollama. Returns (model, raw response text). Raises on connection errors."""
    model = pick_model(cfg)
    if model is None:
        raise RuntimeError("no LLM model available in Ollama")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": cfg["llm"]["temperature"], "num_predict": cfg["llm"]["max_tokens"]},
    }
    r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=180)
    if r.status_code == 400 and "think" in r.text:
        body.pop("think")  # older models do not accept the think switch
        r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=180)
    r.raise_for_status()
    return model, r.json()["message"]["content"]


def _log(con, message_id, job, model, prompt, response, valid, reason):
    if con is None:
        return
    con.execute(
        "INSERT INTO llm_log (message_id, job, model, prompt_text, response_text, valid, reason, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (message_id, job, model, prompt, response, "yes" if valid else "no", reason, now_iso()),
    )
    con.commit()


# ---------- checks ----------

def numbers_in(text):
    """Every whole number written in the text, as strings ('150/95' -> ['150', '95'])."""
    return set(re.findall(r"\d+", text or ""))


def check_extract(cfg, source, data):
    """The four checks for job 1. Returns (fields, reason); reason is None when everything passes."""
    if not isinstance(data, dict):
        return None, "not a JSON object"
    expected = set(EXTRACT_SCHEMA["properties"])
    if set(data) != expected:
        return None, f"fields do not match the schema: {sorted(data)}"
    for key in ("sbp", "dbp"):
        v = data[key]
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
            return None, f"{key} is not a whole number"
    if data["meds_taken"] not in ("yes", "no", "partial", None):
        return None, "meds_taken is not yes, no, partial or empty"
    if not isinstance(data["symptoms"], list) or not isinstance(data["free_text_rest"], str):
        return None, "symptoms or free_text_rest has the wrong type"
    # Every number must appear in the source text.
    src = numbers_in(source)
    for key in ("sbp", "dbp"):
        if data[key] is not None and str(data[key]) not in src:
            return None, f"{key} {data[key]} does not appear in the text"
    if data["sbp"] is not None and data["dbp"] is not None and data["sbp"] < data["dbp"]:
        return None, "sbp is lower than dbp (numbers swapped)"
    # Number checks (PLAN.md step 6): plausible ranges, a minimum gap, and the pair written next to each other.
    pb = cfg.get("plausible_bp", {})
    for key in ("sbp", "dbp"):
        lo, hi = pb.get(key, (0, 999))
        if data[key] is not None and not lo <= data[key] <= hi:
            return None, f"{key} {data[key]} is outside the plausible range {lo}-{hi}"
    if data["sbp"] is not None and data["dbp"] is not None:
        if data["sbp"] - data["dbp"] < pb.get("min_pulse_pressure", 0):
            return None, f"{data['sbp']}/{data['dbp']}: the gap is under {pb.get('min_pulse_pressure')} mmHg"
        nums = [int(n) for n in re.findall(r"\d+", source)]
        if not any(a == data["sbp"] and b == data["dbp"] for a, b in zip(nums, nums[1:])):
            return None, f"{data['sbp']} and {data['dbp']} are not written next to each other in the text"
    for n in numbers_in(data["free_text_rest"]):
        if n not in src:
            return None, f"number {n} in free_text_rest does not appear in the text"
    # Symptom codes only from the allowed list.
    allowed = set(cfg["llm"]["symptom_codes"])
    bad = [s for s in data["symptoms"] if s not in allowed]
    if bad:
        return None, f"symptom codes not on the list: {bad}"
    # Context codes (PLAN.md 5b): only from the list, and each quote must appear word for word in the text.
    vocab = set(cfg.get("context_codes", {}))
    codes = []
    if not isinstance(data["context_codes"], list):
        return None, "context_codes is not a list"
    for item in data["context_codes"]:
        if not isinstance(item, dict) or set(item) != {"code", "quote"}:
            return None, "a context code does not match the schema"
        code, quote = str(item["code"]), str(item["quote"]).strip()
        if code == "none":
            continue
        if code not in vocab:
            return None, f"context code not on the list: {code}"
        if not quote or _norm(quote) not in _norm(source):
            return None, f"the quote for {code} does not appear in the text: {quote[:60]!r}"
        codes.append({"code": code, "quote": quote})
    fields = dict(data)
    fields["symptoms"] = [s for s in data["symptoms"] if s != "none"]
    fields["context_codes"] = codes
    return fields, None


def lexicon_codes(cfg, text):
    """Context codes from config.context_lexicon: a phrase found in the text (case ignored) gives its code, with
    the matched words from the text as the quote."""
    out, low = [], str(text).lower()
    for code, phrases in cfg.get("context_lexicon", {}).items():
        if code == "note" or code not in cfg.get("context_codes", {}):
            continue
        for ph in phrases:
            i = low.find(ph.lower())
            if i >= 0:
                out.append({"code": code, "quote": str(text)[i:i + len(ph)], "source": "lexicon"})
                break
    return out


def _norm(text):
    """Lower case with single spaces, so a quote matches the text word for word but not letter case or spacing."""
    return " ".join(str(text).lower().split())


def check_medications(cfg, source, data):
    if not isinstance(data, dict) or set(data) != {"medications"} or not isinstance(data["medications"], list):
        return None, "fields do not match the schema"
    allowed = {m.lower() for m in cfg["medications"]}
    src = source.lower()
    out = []
    for m in data["medications"]:
        if not isinstance(m, dict) or set(m) != {"name", "dose_text"}:
            return None, "a medication entry does not match the schema"
        name = str(m["name"]).strip().lower()
        if name not in allowed:
            return None, f"'{m['name']}' is not on the config medication list"
        if name not in src:
            return None, f"'{m['name']}' does not appear in the text"
        for n in numbers_in(m["dose_text"]):
            if n not in numbers_in(source):
                return None, f"dose number {n} does not appear in the text"
        out.append({"name": name, "dose_text": str(m["dose_text"]).strip()})
    return out, None


# ---------- job 1: extract ----------

# Words patients use for each symptom code, so the model maps them to the code (a hint, not a rule:
# the code list in the config is still the only thing accepted).
SYMPTOM_HINTS = {
    "headache": "maumivu ya kichwa, kichwa kinauma, maux de tête, mal à la tête",
    "vision_change": "kuona vibaya, macho, vue trouble, blurred vision",
    "chest_pain": "kifua kinauma, maumivu ya kifua, douleur à la poitrine",
    "face_droop": "uso kulegea, uso umepinda, visage tombant",
    "arm_weakness": "mkono dhaifu, hawezi kuinua mkono, bras faible",
    "speech_difficulty": "kushindwa kuongea, maneno hayatoki, difficulté à parler",
    "dizziness": "kizunguzungu, vertiges, étourdi",
}


def _extract_prompt(cfg):
    codes = "\n".join(f"  {c}" + (f" ({SYMPTOM_HINTS[c]})" if c in SYMPTOM_HINTS else "")
                      for c in cfg["llm"]["symptom_codes"] if c != "none")
    return (
        "You copy facts from a patient's SMS or a health worker's note into JSON. The text can be in "
        "Swahili, Kirundi, French or English, with typos. Fields:\n"
        "sbp: the systolic blood pressure (the first, higher number of a reading like 150/95), or null.\n"
        "dbp: the diastolic blood pressure (the second, lower number), or null.\n"
        "If the text has no blood pressure written in digits, sbp and dbp are null.\n"
        "meds_taken: \"yes\" if they took all their medicine, \"partial\" if only some or half, "
        "\"no\" if they did not, null if not mentioned.\n"
        "symptoms: a list of English codes, only these (words patients use in brackets):\n"
        f"{codes}\n"
        "Write the English code, never the patient's word. Empty list if none.\n"
        "free_text_rest: anything else in the message, in a few words, or \"\".\n"
        "context_codes: facts in the text that matter for the health worker's next step, as a list of "
        "{\"code\", \"quote\"}. Codes only from:\n"
        + "\n".join(f"  {c}: {d}" for c, d in cfg.get("context_codes", {}).items() if c != "none")
        + "\nThe quote is the exact words from the text that show the code, copied as written. Empty list if none.\n"
        "symptoms and context_codes are different lists: a side effect or running out of pills is a context code, "
        "not a symptom.\n"
        "Copy numbers exactly as written in digits. Never invent or convert a number. Never give advice.\n"
        "Example. Text: \"Measured after he carried water up the hill. Rested, repeat 136/84. Pills finished last week.\" "
        "Answer: {\"sbp\": 136, \"dbp\": 84, \"meds_taken\": null, \"symptoms\": [], \"free_text_rest\": \"\", "
        "\"context_codes\": [{\"code\": \"measured_after_exertion\", \"quote\": \"Measured after he carried water up the hill\"}, "
        "{\"code\": \"repeat_reading_normal\", \"quote\": \"Rested, repeat 136/84\"}, "
        "{\"code\": \"ran_out_of_pills\", \"quote\": \"Pills finished last week\"}]}\n"
        "More examples (context_codes only):\n"
        "\"Nimesafiri kwenda Kayanza mwezi huu\" -> [{\"code\": \"travelling\", \"quote\": \"Nimesafiri kwenda Kayanza\"}]\n"
        "\"Il habite seul et ne lit pas\" -> [{\"code\": \"lives_alone\", \"quote\": \"Il habite seul\"}, "
        "{\"code\": \"cannot_read\", \"quote\": \"ne lit pas\"}]\n"
        "\"Her ankles are puffy since the new pills\" -> [{\"code\": \"side_effect\", \"quote\": \"Her ankles are puffy since the new pills\"}]\n"
        "\"Nina maumivu makali ya kifua\" -> [{\"code\": \"new_symptom\", \"quote\": \"maumivu makali ya kifua\"}]\n"
        "\"Sikunywa dawa siku tatu\" -> [{\"code\": \"missed_doses\", \"quote\": \"Sikunywa dawa siku tatu\"}]\n"
        "\"Mgonjwa yuko sawa\" -> []"
    )


def _extract_schema(cfg):
    """EXTRACT_SCHEMA with the allowed symptom and context codes as enums, so the model can only pick from the lists."""
    import copy
    schema = copy.deepcopy(EXTRACT_SCHEMA)
    schema["properties"]["symptoms"]["items"] = {"type": "string", "enum": list(cfg["llm"]["symptom_codes"])}
    if cfg.get("context_codes"):
        schema["properties"]["context_codes"]["items"]["properties"]["code"] = {
            "type": "string", "enum": list(cfg["context_codes"])}
    return schema


def extract(cfg, text, con=None, message_id=None):
    """Job 1. Returns {"fields": {...} or None, "valid": bool, "reason": str, "model": str}."""
    prompt = _extract_prompt(cfg)
    try:
        model, raw = _chat(cfg, prompt, text, _extract_schema(cfg))
    except Exception as e:
        reason = f"LLM not available: {e}"
        _log(con, message_id, "extract", pick_model(cfg), text, "", False, reason)
        return {"fields": None, "valid": False, "reason": reason + ". Manual entry.", "model": None}
    data = loads(raw)
    if data is None:
        fields, reason = None, "not valid JSON"
    else:
        fields, reason = check_extract(cfg, text, data)
    _log(con, message_id, "extract", model, f"{prompt}\n---\n{text}", raw, reason is None, reason)
    if fields is not None:
        # The recall fix (PLAN.md 5b): add the codes the phrase list finds that the LLM missed.
        have = {c["code"] for c in fields.get("context_codes", [])}
        fields["context_codes"] = fields.get("context_codes", []) + [c for c in lexicon_codes(cfg, text) if c["code"] not in have]
    return {"fields": fields, "valid": reason is None, "reason": reason or "all four checks passed",
            "model": model, "raw": data}


def medications(cfg, text, con=None, message_id=None):
    """Job 1, medications variant: names and doses from a doctor's recommendation."""
    names = ", ".join(cfg["medications"])
    prompt = ("List the medicines in this doctor's recommendation as JSON: {\"medications\": [{\"name\", "
              f"\"dose_text\"}}]}}. Names only from: {names}, in lower case. dose_text copies the dose as "
              "written, or \"\". Never invent a medicine or a number.")
    try:
        model, raw = _chat(cfg, prompt, text, MEDS_SCHEMA)
    except Exception as e:
        reason = f"LLM not available: {e}"
        _log(con, message_id, "medications", pick_model(cfg), text, "", False, reason)
        return {"medications": None, "valid": False, "reason": reason + ". Manual entry.", "model": None}
    data = loads(raw)
    meds, reason = (None, "not valid JSON") if data is None else check_medications(cfg, text, data)
    _log(con, message_id, "medications", model, f"{prompt}\n---\n{text}", raw, reason is None, reason)
    return {"medications": meds, "valid": reason is None, "reason": reason or "all checks passed", "model": model}


# ---------- job 2: summary ----------

def _summary_facts(con, cfg, patient_id):
    from engine import signals
    encs = con.execute(
        "SELECT date, source, sbp, dbp, meds_taken, symptom_codes FROM encounters WHERE patient_id = ? "
        "ORDER BY date DESC, id DESC LIMIT 2", (patient_id,)
    ).fetchall()
    flags = signals.open_flags(con, patient_id)
    facts = {
        "contacts": [{"date": e["date"][:10], "source": e["source"],
                      "bp": f"{e['sbp']}/{e['dbp']}" if e["sbp"] else None,
                      "medicine_taken": e["meds_taken"],
                      "symptoms": [s for s in loads(e["symptom_codes"], []) if s != "none"]} for e in encs],
        "open_flags": [{"type": f["type"], "value": f["value"]} for f in flags],
    }
    return facts


def _template_summary(facts):
    """The French summary without the LLM, from the same facts."""
    lines = []
    c = facts["contacts"]
    if c:
        last = c[0]
        lines.append(f"Dernier contact le {last['date']} ({last['source']}) : TA {last['bp'] or 'non mesurée'}.")
        if len(c) > 1:
            lines.append(f"Contact précédent le {c[1]['date']} : TA {c[1]['bp'] or 'non mesurée'}.")
        else:
            lines.append("Pas de contact précédent.")
    else:
        lines += ["Aucun contact enregistré.", "Pas de contact précédent."]
    flags = facts["open_flags"]
    lines.append("Alertes : " + ", ".join(f"{f['type']} {f['value']}" for f in flags) + "."
                 if flags else "Aucune alerte ouverte.")
    return lines


FRENCH_WORDS = {"le", "la", "les", "de", "du", "des", "est", "et", "au", "dernier", "contact", "tension", "pas", "une",
                 "un", "alerte", "alertes", "aucune", "précédent", "médicaments", "symptôme", "symptômes", "depuis"}
# Words that would mean the LLM added feelings, advice or a diagnosis that are not in the facts.
FORBIDDEN = ("mieux", "better", "sent ", "feel", "conseil", "devrait", "doit ", "should", "recommand", "diagnos",
             "hypertendu", "souffre", "traitement à", "augmenter la dose", "prescri")


def check_summary(lines, facts_text):
    """Three lines, in French, no new numbers, no feelings, advice or diagnosis. Returns a reason or None."""
    if len(lines) != 3:
        return f"{len(lines)} lines instead of 3"
    text = " ".join(lines).lower()
    extra = set().union(*(numbers_in(x) for x in lines)) - numbers_in(facts_text)
    if extra:
        return f"numbers not in the facts: {sorted(extra)}"
    words = set(re.findall(r"[a-zàâçéèêëîïôûùüÿœ]+", text))
    if len(words & FRENCH_WORDS) < 3:
        return "not written in French"
    bad = [w for w in FORBIDDEN if w in text]
    if bad:
        return f"words not allowed (feelings, advice or diagnosis): {bad}"
    return None


def summary(con, cfg, patient_id):
    """Job 2. Three lines of French from structured fields only. Falls back to a template."""
    facts = _summary_facts(con, cfg, patient_id)
    facts_text = dumps(facts)
    example = ("Exemple de faits: {\"contacts\": [{\"date\": \"2026-05-02\", \"source\": \"sms\", \"bp\": \"152/94\", "
               "\"medicine_taken\": \"partial\", \"symptoms\": [\"headache\"]}, {\"date\": \"2026-04-20\", \"source\": "
               "\"chw\", \"bp\": \"141/88\", \"medicine_taken\": \"yes\", \"symptoms\": []}], \"open_flags\": "
               "[{\"type\": \"trend\", \"value\": \"+11\"}]}\n"
               "Exemple de réponse: {\"lines\": [\"Dernier contact le 2026-05-02 par SMS : TA 152/94, contre 141/88 "
               "le 2026-04-20.\", \"Médicaments pris en partie ; symptôme signalé : maux de tête.\", "
               "\"Alerte ouverte : tendance +11.\"]}")
    prompt = ("Tu écris pour un agent de santé communautaire. Écris exactement trois lignes courtes en français : "
              "ce qui a changé depuis le dernier contact. Utilise uniquement les faits donnés. N'ajoute rien : "
              "pas de ressenti du patient, pas de conseil, pas de diagnostic, pas de nouveau chiffre. "
              "Réponds en JSON {\"lines\": [trois phrases]}.\n" + example)
    fallback = {"lines": _template_summary(facts), "source": "template", "facts": facts}
    try:
        model, raw = _chat(cfg, prompt, facts_text, SUMMARY_SCHEMA)
    except Exception as e:
        _log(con, None, "summary", pick_model(cfg), facts_text, "", False, f"LLM not available: {e}")
        return {**fallback, "reason": "LLM not available"}
    data = loads(raw)
    reason = None
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        reason = "not valid JSON or wrong schema"
    else:
        lines = [str(x).strip() for x in data["lines"] if str(x).strip()]
        reason = check_summary(lines, facts_text)
    _log(con, None, "summary", model, f"{prompt}\n---\n{facts_text}", raw, reason is None, reason)
    if reason:
        return {**fallback, "reason": f"LLM output rejected ({reason}); template used"}
    return {"lines": lines, "source": model, "facts": facts, "reason": "checks passed"}


def main():
    parser = argparse.ArgumentParser(description="Test the local LLM jobs.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--db", default=None)
    parser.add_argument("--text", help="extract fields from this text")
    parser.add_argument("--medications", help="extract medications from this text")
    parser.add_argument("--patient", type=int, help="write the French summary for this patient (needs --db)")
    args = parser.parse_args()
    cfg = load_config(args.config)
    con = connect(args.db) if args.db else None
    print("Ollama:", status(cfg))
    if args.text:
        print(json.dumps(extract(cfg, args.text, con), indent=2, ensure_ascii=False))
    if args.medications:
        print(json.dumps(medications(cfg, args.medications, con), indent=2, ensure_ascii=False))
    if args.patient:
        print(json.dumps(summary(con, cfg, args.patient), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
