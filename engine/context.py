"""PLAN.md 5b: context codes from the free text (CHW notes, complaints, patient SMS), one LLM call per distinct
text. The same extraction and checks as for SMS (engine/llm.py): codes only from config.context_codes, each quote
word for word in the text, else no codes and the text is marked for manual entry.

Usage:
    python engine/context.py --db data/chw.db --config config/guideline_htn.json [--limit 2000]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import llm
from engine.common import connect, dumps, load_config, loads


def codes_for(con, cfg, text, cache, use_llm=True):
    """Context codes for one text (cached by text). Returns (codes, valid, reason).
    use_llm False: the phrase list only (instant; for a demo reset or a bulk import)."""
    if text in cache:
        return cache[text]
    if not use_llm:
        cache[text] = (llm.lexicon_codes(cfg, text), True, "phrase list only")
        return cache[text]
    res = llm.extract(cfg, text, con=con)
    out = ((res["fields"] or {}).get("context_codes") or [], res["valid"], res["reason"])
    cache[text] = out
    return out


def backfill(con, cfg, limit=None, use_llm=True):
    """Fill encounters.context_codes_json for notes and complaints, and parsed_json context_codes for incoming SMS,
    where they are missing. Returns counts."""
    from engine.api import ensure_schema
    ensure_schema(con)
    cache, n_enc, n_msg, failed = {}, 0, 0, 0
    rows = con.execute("SELECT id, chw_notes_text, complaint_text FROM encounters WHERE context_codes_json IS NULL "
                       "AND (chw_notes_text IS NOT NULL OR complaint_text IS NOT NULL)").fetchall()
    for r in rows[:limit] if limit else rows:
        found = []
        for text in (r["chw_notes_text"], r["complaint_text"]):
            if text:
                codes, ok, _ = codes_for(con, cfg, text, cache, use_llm)
                failed += not ok
                found += codes
        con.execute("UPDATE encounters SET context_codes_json = ? WHERE id = ?", (dumps(found), r["id"]))
        n_enc += 1
    msgs = con.execute("SELECT id, text, parsed_json FROM messages WHERE direction = 'in'").fetchall()
    for m in msgs[:limit] if limit else msgs:
        pj = loads(m["parsed_json"], {}) or {}
        if "context_codes" in pj:
            continue
        codes, ok, _ = codes_for(con, cfg, m["text"], cache, use_llm)
        failed += not ok
        pj["context_codes"] = codes
        con.execute("UPDATE messages SET parsed_json = ? WHERE id = ?", (dumps(pj), m["id"]))
        n_msg += 1
    con.commit()
    return {"encounters": n_enc, "messages": n_msg, "distinct_texts": len(cache), "failed_checks": failed}


def main():
    parser = argparse.ArgumentParser(description="Context codes for notes and SMS (PLAN.md 5b).")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--lexicon-only", action="store_true", help="the phrase list only, no LLM (instant)")
    args = parser.parse_args()
    print(backfill(connect(args.db), load_config(args.config), args.limit, use_llm=not args.lexicon_only))


if __name__ == "__main__":
    main()
