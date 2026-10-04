"""Step 4. Decider: rules first, then Julia-1 behind a one-step guardrail (PLAN.md step 4, CLAUDE.md).

1. Rules: any emergency flag returns emergency_now at once, with the flag's reason; no model is asked.
   Otherwise rule_default = the highest action named by any flag (threshold, trend, symptom, WHO risk), else routine.
2. State, in this order: each flag's type and reason; "Rule default: <action>"; adherence (medicine taken at the
   last three contacts) and days since last contact; "CHW notes:" from the last two encounters verbatim;
   "Patient messages:" the last three incoming messages verbatim, in their own language.
   A reading never appears on its own: only inside the flag that interprets it.
3. Julia-1 reads the state with the options from config.actions and the criteria from config.action_criteria.
   The model reviewed at the monthly review, when there is one, is averaged in.
4. Guardrail: at most config.julia.decide_max_steps on the ladder routine, recheck_7_days, visit_this_week,
   refer_clinic; never down from config.julia.never_step_down_from; emergency_now may always be chosen
   (escalation, a person confirms). Top probability below min_confidence, or the top two closer than margin,
   gives not_sure.
5. Output: the action, the rule default, whether the model moved it and which way, the probabilities, and the
   note or message sentence that moved it (found by asking again without each sentence). A move is kept only
   when that sentence lowers the chosen action's probability by config.julia.decide_min_text_effect or more.
If config.julia.decide is false, or no model is available, the rule default is returned ("rules only").

Usage:
    python engine/decide.py --db data/chw.db --config config/guideline_htn.json --patient 1
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import models, signals
from engine.common import (action_rank, add_days, audit, connect, days_between, dumps, get_meta, iso,
                           last_contact_date, load_config, loads, now_iso)

QUESTION = "Given the rule default and the notes, what should the CHW do next?"
LADDER = ["routine", "recheck_7_days", "visit_this_week", "refer_clinic"]

# Short names of each action, for reasons shown to the CHW.
ACTION_TEXT = {
    "routine": "Routine care",
    "recheck_7_days": "Recheck in 7 days",
    "visit_this_week": "Visit this week",
    "refer_clinic": "Refer to the clinic",
    "emergency_now": "Emergency: follow the referral protocol",
    "not_sure": "Not sure, ask a nurse",
}


# ---------- state ----------

def build_state(con, cfg, patient_id, as_of=None, sig=None):
    """The state Julia-1 reads, as text, plus the facts as a dict. state["sentences"] lists the notes and
    messages one by one, so the decider can say which one moved the decision."""
    as_of = iso(as_of)
    sig = sig or signals.compute(con, cfg, patient_id, as_of)
    flags = sig["flags"]
    rule_default = rule_action(cfg, flags)
    traj = con.execute(
        "SELECT flag, reason_text FROM trajectory_scores WHERE patient_id = ? AND date <= ? "
        "ORDER BY date DESC, id DESC LIMIT 1", (patient_id, as_of)).fetchone()
    meds = [r["meds_taken"] for r in con.execute(
        "SELECT meds_taken FROM encounters WHERE patient_id = ? AND date <= ? AND meds_taken IS NOT NULL "
        "ORDER BY date DESC, id DESC LIMIT 3", (patient_id, as_of + "T99")).fetchall()]
    contact = last_contact_date(con, patient_id, as_of)
    days_silent = days_between(contact, as_of) if contact else None
    notes = [dict(r) for r in con.execute(
        "SELECT date, chw_notes_text FROM encounters WHERE patient_id = ? AND date <= ? "
        "ORDER BY date DESC, id DESC LIMIT 2", (patient_id, as_of + "T99")).fetchall() if r["chw_notes_text"]]
    msgs = [dict(r) for r in con.execute(
        "SELECT received_at, text FROM messages WHERE patient_id = ? AND direction = 'in' AND kind != 'quiz' "
        "AND substr(received_at, 1, 10) <= ? ORDER BY received_at DESC, id DESC LIMIT 3",
        (patient_id, as_of)).fetchall()]

    lines = []
    for f in flags:
        lines.append(f"Flag {f['type']}: {f['reason_text']}")
    if traj and traj["flag"] == "yes":
        lines.append(f"Flag peer_trajectory: {traj['reason_text']}")
    if not lines:
        lines.append("Flags: none.")
    lines.append(f"Rule default: {rule_default}.")
    lines.append("Medicine taken at the last three contacts: " + (", ".join(meds) if meds else "not recorded") + ".")
    lines.append(f"Days since last contact: {days_silent if days_silent is not None else 'unknown'}.")
    sentences = []
    if notes:
        lines.append("CHW notes:")
        for n in reversed(notes):
            text = n["chw_notes_text"].strip()
            lines.append(f"\"{text}\"")
            sentences.append({"kind": "CHW note", "date": n["date"][:10], "text": text})
    if msgs:
        lines.append("Patient messages:")
        for m in reversed(msgs):
            text = m["text"].strip()
            lines.append(f"\"{text}\"")
            sentences.append({"kind": "patient message", "date": m["received_at"][:10], "text": text})

    state = {
        "flags": [{"type": f["type"], "value": f["value"], "emergency": f["emergency"],
                   **({"urgent_days": f["urgent_days"]} if f.get("urgent_days") else {})} for f in flags],
        "rule_default": rule_default,
        "meds_last_three": meds,
        "days_since_contact": days_silent,
        "trajectory": traj["reason_text"] if traj and traj["flag"] == "yes" else None,
        "n_readings": sig["n_readings"],
        "latest_bp": sig["latest"],
        "sentences": sentences,
    }
    return "\n".join(lines), state


def _understanding(con, cfg, patient_id, as_of):
    """Share of correct quiz answers per topic over the window (None when never asked)."""
    since = add_days(as_of, -cfg["quiz"]["window_days"])
    out = {}
    for topic in cfg["quiz"]["topics"]:
        rows = con.execute(
            "SELECT correct FROM quiz_results WHERE patient_id = ? AND topic = ? AND answer IS NOT NULL "
            "AND correct IN ('yes', 'no') AND substr(asked_at, 1, 10) BETWEEN ? AND ?",
            (patient_id, topic, since, as_of),
        ).fetchall()
        out[topic] = round(sum(r["correct"] == "yes" for r in rows) / len(rows), 2) if rows else None
    return out


# ---------- rules ----------

def rule_action(cfg, flags):
    """The rule default: the most urgent action named by any flag; routine when there are none."""
    best = "routine"
    for f in flags:
        a = f.get("action")
        if a and action_rank(cfg, a) > action_rank(cfg, best):
            best = a
    return best


def model_version(con, cfg=None):
    """Which models make the suggestion right now."""
    if not cfg or not models.julia_on(cfg, "decide"):
        return "rules-v1"
    parts = ["rules"]
    if models.julia(cfg) is not None:  # loads Julia-1 on first use, so status reports the real decider
        parts.append("julia-1")
    lr = get_meta(con, "decider_version")
    if lr and models.load_pickle(cfg, f"decider_{lr}.pkl") is not None:
        parts.append(lr)
    return "+".join(parts) if len(parts) > 1 else "rules-v1"


def _model_probs(con, cfg, state_text):
    """Average the probabilities of Julia-1 and the reviewed model. None when neither is available."""
    options = {a: cfg.get("action_criteria", {}).get(a, ACTION_TEXT.get(a, a)) for a in cfg["actions"]}
    found = []
    out = models.julia_choose(cfg, state_text, QUESTION, options)
    if out:
        found.append(out[1])
    version = get_meta(con, "decider_version")
    if version:
        lr = models.load_pickle(cfg, f"decider_{version}.pkl")
        if lr is not None:
            found.append(models.predict_proba(lr, state_text))
    if not found:
        return None
    return {a: sum(p.get(a, 0.0) for p in found) / len(found) for a in cfg["actions"]}


def guardrail(cfg, rule_default, choice):
    """Apply the one-step rule. Returns (final action, what happened)."""
    j = cfg.get("julia", {})
    max_steps = j.get("decide_max_steps", 1)
    if choice == "emergency_now":
        return choice, "escalated by the text"
    if choice not in LADDER or rule_default not in LADDER:
        return rule_default, "kept the rule default"
    steps = LADDER.index(choice) - LADDER.index(rule_default)
    if steps < 0 and rule_default in j.get("never_step_down_from", []):
        return rule_default, f"never steps down from {rule_default}"
    if abs(steps) > max_steps:
        clamped = LADDER[LADDER.index(rule_default) + (max_steps if steps > 0 else -max_steps)]
        return clamped, f"clamped from {choice} to {max_steps} step"
    return choice, "within one step" if steps else "kept the rule default"


def _moved_by(con, cfg, state_text, state, choice):
    """The note or message whose removal lowers the model's chosen action's probability the most."""
    best, drop = None, 0.0
    base = (_model_probs(con, cfg, state_text) or {}).get(choice, 0.0)
    for s in state["sentences"]:
        without = state_text.replace(f"\"{s['text']}\"", "", 1)
        p = (_model_probs(con, cfg, without) or {}).get(choice, 0.0)
        if base - p > drop:
            best, drop = s, base - p
    return {**best, "probability_drop": round(drop, 2)} if best else None


# ---------- Design B, "facts first" (PLAN.md section 5b) ----------

FACTS_QUESTION = "Given the rule default and the facts, should the CHW keep it, step up or step down?"


def _codes_in_window(con, patient_id, as_of, window_days=30):
    """Context codes with quotes from the last two encounters and the last three incoming messages, only from the
    last config.context_window_days (an old episode, such as chest pain months ago, never moves today's decision)."""
    found = []
    since = add_days(as_of, -window_days)
    for r in con.execute("SELECT date, source, context_codes_json FROM encounters WHERE patient_id = ? AND date <= ? "
                         "AND date >= ? ORDER BY date DESC, id DESC LIMIT 2", (patient_id, as_of + "T99", since)).fetchall():
        for c in loads(r["context_codes_json"], []) or []:
            found.append({**c, "date": r["date"][:10], "kind": "patient SMS" if r["source"] == "sms" else "CHW note"})
    for r in con.execute("SELECT received_at, parsed_json FROM messages WHERE patient_id = ? AND direction = 'in' "
                         "AND kind != 'quiz' AND substr(received_at, 1, 10) <= ? AND substr(received_at, 1, 10) >= ? "
                         "ORDER BY received_at DESC, id DESC LIMIT 3", (patient_id, as_of, since)).fetchall():
        for c in (loads(r["parsed_json"], {}) or {}).get("context_codes") or []:
            found.append({**c, "date": r["received_at"][:10], "kind": "patient message"})
    seen, out = set(), []
    for c in found:
        if (c["code"], c["quote"]) not in seen:
            seen.add((c["code"], c["quote"]))
            out.append(c)
    return out


def build_state_facts(con, cfg, patient_id, as_of=None, sig=None):
    """The short English state Julia-1 reads in Design B: flags, rule default, medicine, days silent, and the
    context codes with their quotes. No raw message text."""
    as_of = iso(as_of)
    sig = sig or signals.compute(con, cfg, patient_id, as_of)
    _, base = build_state(con, cfg, patient_id, as_of, sig)
    codes = _codes_in_window(con, patient_id, as_of, cfg.get("context_window_days", 30))
    lines = [f"Flag {f['type']}: {f['reason_text']}" for f in sig["flags"]] or ["Flags: none."]
    lines.append(f"Rule default: {base['rule_default']}.")
    lines.append("Medicine taken at the last three contacts: " + (", ".join(base["meds_last_three"]) or "not recorded") + ".")
    lines.append(f"Days since last contact: {base['days_since_contact'] if base['days_since_contact'] is not None else 'unknown'}.")
    sentences = []
    if codes:
        lines.append("Facts from the notes and messages:")
        for c in codes:
            line = f"{c['code']}: \"{c['quote']}\""
            lines.append(line)
            sentences.append({"kind": c["kind"], "date": c["date"], "text": c["quote"], "code": c["code"], "line": line})
    else:
        lines.append("Facts from the notes and messages: none.")
    state = {**base, "sentences": sentences, "context_codes": codes}
    return "\n".join(lines), state


def _facts_options(cfg):
    """The three options in plain words (Julia-1 allows 48 tokens per option): the codes from config.step_criteria."""
    sc = cfg["step_criteria"]
    words = lambda c: c.replace("_", " ")  # noqa: E731
    up = ", ".join(words(c) for c in sc["step_up"])
    down = "; or ".join(" with ".join(words(c) for c in combo) for combo in sc["step_down"])
    return {"keep": f"Keep the rule default: {sc.get('keep', 'no code applies')}",
            "step_up": f"Step up one level: {up}",
            "step_down": f"Step down one level: {down}"}


def table_decider(cfg, rule_default, codes):
    """The comparison from PLAN.md 5b: apply config.step_criteria to the codes directly, without a model.
    Returns keep, step_up or step_down (both directions present: keep, and say so)."""
    have = {c["code"] for c in codes}
    up = any(c in have for c in cfg["step_criteria"]["step_up"])
    down = any(all(c in have for c in combo) for combo in cfg["step_criteria"]["step_down"])
    if up and down:
        return "keep", "facts point both ways"
    return ("step_up", "a step-up fact is present") if up else ("step_down", "a step-down combination is present") \
        if down else ("keep", "no code applies")


def _step(cfg, rule_default, answer):
    """keep / step_up / step_down to an action on the ladder, with the Design A guardrail."""
    if answer == "keep" or rule_default not in LADDER:
        return rule_default, "kept the rule default"
    i = LADDER.index(rule_default)
    if answer == "step_down":
        if rule_default in cfg.get("julia", {}).get("never_step_down_from", []):
            return rule_default, f"never steps down from {rule_default}"
        return (LADDER[i - 1], "one step down") if i > 0 else (rule_default, "already at the bottom")
    return (LADDER[i + 1], "one step up") if i + 1 < len(LADDER) else (rule_default, "already at the top")


def _facts_probs(cfg, state_text):
    out = models.julia_choose(cfg, state_text, FACTS_QUESTION, _facts_options(cfg))
    return out[1] if out else None


def suggest_facts(con, cfg, patient_id, as_of, sig, out):
    """Design B after the emergency and no-information checks. mode "facts": Julia-1 on the facts state;
    mode "table": the lookup table on the codes."""
    mode = cfg["julia"].get("decide_mode", "actions")
    state_text, state = build_state_facts(con, cfg, patient_id, as_of, sig)
    rule = state["rule_default"]
    flag_reasons = " ".join(f["reason_text"] for f in sig["flags"]) or "No flags."
    out.update(state_text=state_text, state=state, guideline_ref=", ".join(sorted({f["guideline_ref"] for f in sig["flags"]})))
    codes = state["context_codes"]
    if mode == "table":
        answer, why = table_decider(cfg, rule, codes)
        final, what = _step(cfg, rule, answer)
        out.update(suggestion=final, probs={answer: 1.0}, backend="rules+table", model_version="rules+table-v1",
                   guardrail=f"{why}; {what}")
    else:
        probs = _facts_probs(cfg, state_text)
        if probs is None:
            out.update(suggestion=rule, probs={rule: 1.0}, backend="rules", model_version=model_version(con, cfg),
                       reason_text=f"{ACTION_TEXT[rule]} (rules only: Julia-1 not available). {flag_reasons}")
            return out
        ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
        (answer, p1), p2 = ranked[0], ranked[1][1]
        out.update(probs=probs, backend="rules+julia-1 (facts)", model_version="rules+julia-1-facts")
        if p1 < cfg["min_confidence"] or (p1 - p2) < cfg["margin"]:
            out.update(suggestion="not_sure", reason_text=(f"Not sure: '{answer}' at {p1:.2f}, next at {p2:.2f}. "
                                                           f"Ask a nurse. Rule default: '{rule}'. {flag_reasons}"))
            return out
        final, what = _step(cfg, rule, answer)
        out.update(suggestion=final, guardrail=what)
        if final != rule:
            # Which code moved it: the one whose removal lowers the chosen answer the most.
            best, drop = None, 0.0
            for s in state["sentences"]:
                p = (_facts_probs(cfg, state_text.replace(s["line"], "", 1)) or {}).get(answer, 0.0)
                if p1 - p > drop:
                    best, drop = s, p1 - p
            need = cfg["julia"].get("decide_min_text_effect", 0.0)
            if best is None or drop < need:
                out.update(suggestion=rule, guardrail=f"kept the rule default: no fact caused the move to '{final}'",
                           reason_text=f"{ACTION_TEXT[rule]}: the rule default, kept (no fact caused a move). {flag_reasons}")
                return out
            out["moved_by"] = {**best, "probability_drop": round(drop, 2)}
    final = out["suggestion"]
    if final != rule:
        out["moved"] = True
        out["direction"] = "up" if action_rank(cfg, final) > action_rank(cfg, rule) else "down"
        if out.get("moved_by") is None:  # the table: name the code that applied
            listed = set(cfg["step_criteria"]["step_up"]) if out["direction"] == "up" else \
                {c for combo in cfg["step_criteria"]["step_down"] for c in combo}
            hit = next((c for c in codes if c["code"] in listed), None)
            out["moved_by"] = {"code": hit["code"], "text": hit["quote"], "kind": hit["kind"], "date": hit["date"]} if hit else None
        mb = out["moved_by"]
        because = f" because of {mb['code']}: \"{mb['text']}\" ({mb['kind']}, {mb['date']})" if mb else ""
        out["reason_text"] = f"Rule default '{rule}'; moved {out['direction']} to '{final}'{because}. {flag_reasons}"
    else:
        out["reason_text"] = f"{ACTION_TEXT[rule]}: the rule default, kept. {flag_reasons}"
    return out


# ---------- decide ----------

def suggest(con, cfg, patient_id, as_of=None):
    """Work out the suggestion without storing it."""
    as_of = iso(as_of)
    sig = signals.compute(con, cfg, patient_id, as_of)
    state_text, state = build_state(con, cfg, patient_id, as_of, sig)
    flags = sig["flags"]
    rule = state["rule_default"]
    flag_reasons = " ".join(f["reason_text"] for f in flags) or "No flags."
    refs = ", ".join(sorted({f["guideline_ref"] for f in flags}))
    out = {"patient_id": patient_id, "date": as_of, "state_text": state_text, "state": state, "flags": flags,
           "rule_default": rule, "rule_action": rule, "moved": False, "direction": None, "moved_by": None,
           "guardrail": None, "config_version": cfg["config_version"], "approved": cfg["approved"]}

    # 1. Emergencies bypass the model.
    emergency = [f for f in flags if f["emergency"] == "yes"]
    if emergency:
        out.update(suggestion="emergency_now", probs={"emergency_now": 1.0}, backend="rule",
                   model_version="none (emergency rule)",
                   reason_text=" ".join(f["reason_text"] for f in emergency),
                   guideline_ref=", ".join(sorted({f["guideline_ref"] for f in emergency})))
        return out

    # Nothing known about the patient.
    has_msg = con.execute("SELECT 1 FROM messages WHERE patient_id = ? AND direction = 'in' LIMIT 1",
                          (patient_id,)).fetchone()
    if sig["n_readings"] == 0 and not has_msg:
        out.update(suggestion="not_sure", probs={}, backend="rule", model_version=model_version(con, cfg),
                   reason_text="Not enough information: no readings and no messages. Ask a nurse.", guideline_ref="")
        return out

    # Design B ("facts first", PLAN.md 5b) when config.julia.decide_mode says so; Design A below is untouched.
    mode = cfg["julia"].get("decide_mode", "actions")
    if mode == "table" or (mode == "facts" and models.julia_on(cfg, "decide")):
        return suggest_facts(con, cfg, patient_id, as_of, sig, out)

    # 3. The model, unless the config says rules only or no model is available.
    probs = _model_probs(con, cfg, state_text) if models.julia_on(cfg, "decide") else None
    if probs is None:
        why = "rules only" if not models.julia_on(cfg, "decide") else "rules only (no model available)"
        out.update(suggestion=rule, probs={rule: 1.0}, backend="rules", model_version=model_version(con, cfg),
                   reason_text=f"{ACTION_TEXT[rule]} ({why}). {flag_reasons}", guideline_ref=refs)
        return out

    backend = model_version(con, cfg)
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top, p1 = ranked[0]
    p2 = ranked[1][1] if len(ranked) > 1 else 0.0

    # 4. Confidence rule, then the guardrail.
    if p1 < cfg["min_confidence"] or (p1 - p2) < cfg["margin"] or top == "not_sure":
        out.update(suggestion="not_sure", probs=probs, backend=backend, model_version=backend,
                   reason_text=(f"Not sure: top option '{top}' at {p1:.2f}, next at {p2:.2f} "
                                f"(needs {cfg['min_confidence']} and a margin of {cfg['margin']}). Ask a nurse. "
                                f"Rule default: '{rule}'. {flag_reasons}"), guideline_ref=refs)
        return out
    final, what = guardrail(cfg, rule, top)
    out.update(suggestion=final, probs=probs, backend=backend, model_version=backend, guardrail=what,
               guideline_ref=refs)

    # 5. Say what moved it. A move is kept only when a note or message caused it (decide_min_text_effect).
    if final != rule:
        moved_by = _moved_by(con, cfg, state_text, state, top)  # the effect on what the model chose
        need = cfg.get("julia", {}).get("decide_min_text_effect", 0.0)
        if moved_by is None or moved_by["probability_drop"] < need:
            what = (f"kept the rule default: no note or message caused the move to '{final}' "
                    f"(needs a probability drop of {need})")
            out["guardrail"] = what
            out["reason_text"] = f"{ACTION_TEXT[rule]}: the rule default, kept. The model chose '{top}'; {what}. {flag_reasons}"
            out["suggestion"] = rule
            return out
    if final != rule:
        out["moved"] = True
        out["direction"] = "up" if action_rank(cfg, final) > action_rank(cfg, rule) else "down"
        out["moved_by"] = moved_by
        because = (f" because of the {out['moved_by']['kind']} of {out['moved_by']['date']}: "
                   f"\"{out['moved_by']['text']}\"" if out["moved_by"] else " (no single note or message stood out)")
        out["reason_text"] = (f"Rule default '{rule}'; moved {out['direction']} to '{final}' "
                              f"(p={probs[top]:.2f}, {what}){because}. {flag_reasons}")
    else:
        note = f" The model chose '{top}'; {what}." if top != final else ""
        out["reason_text"] = f"{ACTION_TEXT[rule]}: the rule default, kept (p={probs.get(final, 0):.2f}).{note} {flag_reasons}"
    return out


def decide(con, cfg, patient_id, as_of=None, store=True):
    """Suggest an action and store it in the decisions table (replacing today's unconfirmed one)."""
    out = suggest(con, cfg, patient_id, as_of)
    if store:
        con.execute(
            "DELETE FROM decisions WHERE patient_id = ? AND date = ? AND final_choice IS NULL",
            (patient_id, out["date"]),
        )
        cur = con.execute(
            "INSERT INTO decisions (patient_id, date, state_json, suggestion, probs_json, model_version, config_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (patient_id, out["date"], dumps({"text": out["state_text"], "reason_text": out["reason_text"],
                                             "guideline_ref": out["guideline_ref"], "backend": out["backend"],
                                             **out["state"]}), out["suggestion"],
             dumps(out["probs"]), out["model_version"], out["config_version"]),
        )
        out["decision_id"] = cur.lastrowid
        con.commit()
    return out


def confirm(con, cfg, patient_id, final_choice, override_reason="", as_of=None):
    """Store the CHW's choice on the latest decision, then ask followups.py to schedule what it implies."""
    if final_choice not in cfg["actions"]:
        return {"error": f"final_choice must be one of {cfg['actions']}"}
    row = con.execute(
        "SELECT * FROM decisions WHERE patient_id = ? ORDER BY date DESC, id DESC LIMIT 1", (patient_id,)
    ).fetchone()
    if row is None:
        return {"error": "no suggestion to confirm; call decide first"}
    overridden = final_choice != row["suggestion"]
    con.execute(
        "UPDATE decisions SET final_choice = ?, override_reason = ? WHERE id = ?",
        (final_choice, override_reason or None, row["id"]),
    )
    audit(con, "override" if overridden else "confirm", "decisions", row["id"])

    con.commit()
    from engine import followups
    state = loads(row["state_json"], {}) or {}
    urgent = any(f.get("urgent_days") for f in state.get("flags", []))
    created = followups.create_after_decision(con, cfg, patient_id, final_choice, urgent=urgent, as_of=as_of)
    return {"decision_id": row["id"], "suggestion": row["suggestion"], "final_choice": final_choice,
            "overridden": overridden, "override_reason": override_reason, "urgent": urgent,
            "followups": created, "followup": created[0] if created else None,
            "model_version": row["model_version"], "config_version": row["config_version"]}


def main():
    parser = argparse.ArgumentParser(description="Suggest the next action for one patient.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--patient", type=int, required=True)
    parser.add_argument("--date", default=None)
    parser.add_argument("--no-store", action="store_true")
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    out = decide(con, cfg, args.patient, args.date, store=not args.no_store)
    print(json.dumps({k: v for k, v in out.items() if k != "state"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
