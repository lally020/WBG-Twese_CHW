"""WHO 10-year cardiovascular risk, from the non-laboratory chart for Eastern Sub-Saharan Africa
(config/who_risk_eastern_ssa.json, read from page 4 of the WHO PDF; source WHO2019).

The chart needs age, sex, smoking, systolic BP and BMI. It covers ages 40 to 74. Outside that range
config.who_risk says what to do:
    age_above_chart: "use_top_band" (75 and over read as 70-74, marked "risk likely higher") or "no_risk"
    age_below_chart: "no_risk" (under 40: no chart value; the BP rules still apply) or "use_bottom_band"
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import root_path

_cache = {}


def load_table(cfg):
    """The chart as a dict, or None when the file is missing. Re-read when the file changes."""
    path = root_path(cfg.get("who_risk", {}).get("table", "config/who_risk_eastern_ssa.json"))
    if not os.path.exists(path):
        return None
    stamp = os.path.getmtime(path)
    if _cache.get(path, (None, None))[0] != stamp:
        with open(path, encoding="utf-8") as f:
            _cache[path] = (stamp, json.load(f))
    return _cache[path][1]


def _band(value, bands):
    """Index of the band that holds value. Bands are read by their lower edge, so 24.95 falls in 20-24.99."""
    idx = None
    for i, (lo, _) in enumerate(bands):
        if value >= lo:
            idx = i
    return idx


def lookup(cfg, patient, sbp):
    """The patient's 10-year risk. Returns a dict with percent (or None) and how it was read."""
    table = load_table(cfg)
    if table is None:
        return {"percent": None, "reason": "WHO table not loaded"}
    missing = [k for k in ("age", "sex", "smoker", "height_cm", "weight_kg") if not patient[k]]
    if sbp is None:
        missing.append("systolic BP")
    if missing:
        return {"percent": None, "reason": f"missing: {', '.join(missing)}"}
    rules = cfg.get("who_risk", {})
    age = patient["age"]
    lo_age, hi_age = table["age_bands"][0][0], table["age_bands"][-1][1]
    note = None
    if age > hi_age:
        if rules.get("age_above_chart", "use_top_band") != "use_top_band":
            return {"percent": None, "reason": f"age {age} is above the chart ({lo_age}-{hi_age})"}
        note = f"age {age} is above the chart: read as {table['age_bands'][-1][0]}-{hi_age}, so the risk is likely higher"
    if age < lo_age:
        if rules.get("age_below_chart", "no_risk") != "use_bottom_band":
            return {"percent": None, "reason": f"age {age} is below the chart ({lo_age}-{hi_age}); BP rules still apply"}
        note = f"age {age} is below the chart: read as {lo_age}-{table['age_bands'][0][1]}"
    bmi = patient["weight_kg"] / (patient["height_cm"] / 100) ** 2
    a = _band(min(max(age, lo_age), hi_age), table["age_bands"])
    s = _band(sbp, table["sbp_bands"])
    b = _band(bmi, table["bmi_bands"])
    alo, ahi = table["age_bands"][a]
    pct = table["risk_percent"][f"{patient['sex']}|{patient['smoker']}|{alo}-{ahi}"][s][b]
    slo, shi = table["sbp_bands"][s]
    blo, bhi = table["bmi_bands"][b]
    sbp_txt = f"SBP {slo}-{shi}" if shi < 400 else f"SBP {slo}+"
    bmi_txt = f"BMI {blo:g}-{int(bhi)}" if bhi < 100 else f"BMI {blo:g}+"
    desc = (f"{'man' if patient['sex'] == 'male' else 'woman'}, {'smoker' if patient['smoker'] == 'yes' else 'non-smoker'}, "
            f"age {alo}-{ahi}, {sbp_txt}, {bmi_txt} ({bmi:.1f})")
    return {"percent": pct, "description": desc, "age_note": note, "bmi": round(bmi, 1),
            "points": pct * table.get("score_per_percent", 1.0)}


def level(cfg, pct):
    """The highest config.who_risk level the percent reaches, or None."""
    hit = None
    for lv in cfg.get("who_risk", {}).get("levels", []):
        if pct is not None and pct >= lv["risk_gte"] and (hit is None or lv["risk_gte"] > hit["risk_gte"]):
            hit = lv
    return hit


def flag(cfg, patient, sbp, reading_date=None):
    """A who_risk flag when the 10-year risk reaches a config level, else None."""
    r = lookup(cfg, patient, sbp)
    lv = level(cfg, r["percent"])
    if lv is None:
        return None
    when = f" on the reading of {reading_date[:10]}" if reading_date else ""
    reason = (f"WHO 10-year CVD risk {r['percent']}% ({r['description']}){when}: at or above the "
              f"'{lv['id']}' level ({lv['risk_gte']}%).")
    if r["age_note"]:
        reason += f" Note: {r['age_note']}."
    return {"type": "who_risk", "value": f"{r['percent']}%", "reason_text": reason,
            "guideline_ref": lv["source"], "emergency": "no", "action": lv["action"]}
