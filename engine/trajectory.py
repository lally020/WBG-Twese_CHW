"""Step 11b. Peer trajectories: compare a patient's recent pattern with local patients who went on
to a stroke or an admission.

For every patient and every past reading, a window of the last config.trajectory.window_readings
readings becomes features: the systolic values, slope, spread, adherence, symptoms, longest silence,
quiz understanding. A historical window is positive when the patient had an event within
config.trajectory.horizon_days after it (only windows that end before the event are used).
A distance-weighted k-nearest-neighbours search over other patients' windows gives, for the current
window, the share of similar local patterns that were followed by an event, and the closest peers.

When the events table holds fewer than config.trajectory.min_events rows, the share is empty, the flag
is no, and the reason says "not enough local events yet", so the rules carry the load.

Built for one clinic laptop: the windows are computed with numpy and searched with one shared
nearest-neighbour index (scikit-learn), kept in memory until the date or the number of events changes.
After a new SMS, only that patient's current window is recomputed and searched against the index.

Usage:
    python engine/trajectory.py --db data/chw.db --config config/guideline_htn.json [--date 2026-10-03]
    python engine/trajectory.py ... --min-events 5    (lower the minimum to show the mechanism; demo only)
"""

import argparse
import os
import sys
from datetime import date

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.common import connect, dumps, iso, load_config

MODEL_VERSION = "knn-v2"
DEFAULT_UNDERSTANDING = 0.7  # used when a patient has never answered a quiz question
_cache = {}


# ---------- loading ----------

def _db_path(con):
    return con.execute("PRAGMA database_list").fetchone()[2]


def _load(con, as_of, patient_id=None):
    """Readings, first events and quiz answers up to as_of, as numpy arrays per patient."""
    import pandas as pd
    where, params = "sbp IS NOT NULL AND date <= ?", [as_of + "T99"]
    if patient_id is not None:
        where += " AND patient_id = ?"
        params.append(patient_id)
    df = pd.read_sql_query(f"SELECT patient_id, date, sbp, meds_taken, symptom_codes FROM encounters "
                           f"WHERE {where} ORDER BY patient_id, date, id", con, params=params)
    series = {}
    if len(df):
        df["day"] = pd.to_datetime(df["date"].str.slice(0, 10)).map(pd.Timestamp.toordinal)
        df["meds_known"] = df["meds_taken"].notna().astype(float)
        df["meds_yes"] = (df["meds_taken"] == "yes").astype(float)
        codes = df["symptom_codes"].fillna("")
        df["n_sym"] = codes.str.count(r'"[a-z_]+"') - codes.str.count(r'"none"')
        for pid, g in df.groupby("patient_id", sort=False):
            series[int(pid)] = {"sbp": g["sbp"].to_numpy(float), "day": g["day"].to_numpy(np.int64),
                                "dates": g["date"].str.slice(0, 10).tolist(),
                                "meds_known": g["meds_known"].to_numpy(), "meds_yes": g["meds_yes"].to_numpy(),
                                "n_sym": g["n_sym"].to_numpy(float)}
    q = pd.read_sql_query("SELECT patient_id, asked_at, correct FROM quiz_results WHERE correct IN ('yes','no') "
                          + ("AND patient_id = ? " if patient_id is not None else "") + "ORDER BY patient_id, asked_at",
                          con, params=[patient_id] if patient_id is not None else [])
    quiz = {}
    if len(q):
        q["day"] = pd.to_datetime(q["asked_at"].str.slice(0, 10)).map(pd.Timestamp.toordinal)
        for pid, g in q.groupby("patient_id", sort=False):
            quiz[int(pid)] = (g["day"].to_numpy(np.int64), np.cumsum((g["correct"] == "yes").to_numpy(float)))
    events = {int(r[0]): date.fromisoformat(r[1][:10]).toordinal() for r in con.execute(
        "SELECT patient_id, MIN(date) FROM events WHERE date <= ? GROUP BY patient_id", (as_of,)).fetchall()}
    return series, quiz, events


def _windows(s, q, w):
    """All windows of w readings for one patient: features (m, w+6) and the end day of each window."""
    from numpy.lib.stride_tricks import sliding_window_view as slide
    n = len(s["sbp"])
    if n < w:
        return None, None
    W = slide(s["sbp"], w)
    x = np.arange(w) - (w - 1) / 2
    slope = W @ (x / (x ** 2).sum())
    spread = W.std(axis=1)

    def win_sum(a):
        cs = np.concatenate([[0.0], np.cumsum(a)])
        return cs[w:] - cs[:-w]

    known, yes = win_sum(s["meds_known"]), win_sum(s["meds_yes"])
    adherence = np.where(known > 0, yes / np.maximum(known, 1), 1.0)
    symptoms = win_sum(s["n_sym"])
    gaps = np.diff(s["day"]).astype(float)
    maxgap = slide(gaps, w - 1).max(axis=1) if w > 1 else np.zeros(len(W))
    end = s["day"][w - 1:]
    if q is not None:
        idx = np.searchsorted(q[0], end, side="right")
        correct = np.where(idx > 0, q[1][np.maximum(idx - 1, 0)], 0.0)
        understanding = np.where(idx > 0, correct / np.maximum(idx, 1), DEFAULT_UNDERSTANDING)
    else:
        understanding = np.full(len(W), DEFAULT_UNDERSTANDING)
    feats = np.column_stack([W, slope, spread, adherence, symptoms, maxgap, understanding])
    return feats, end


# ---------- the index of historical windows ----------

def build_index(con, cfg, as_of=None, use_cache=True):
    """Every historical window with its label, standardised, in one nearest-neighbour index."""
    from sklearn.neighbors import NearestNeighbors
    as_of = iso(as_of)
    n_events = con.execute("SELECT COUNT(*) FROM events WHERE date <= ?", (as_of,)).fetchone()[0]
    key = (_db_path(con), as_of, n_events)
    if use_cache and key in _cache:
        return _cache[key]
    t = cfg["trajectory"]
    w = t["window_readings"]
    series, quiz, events = _load(con, as_of)
    X, y, owner, end_day, row = [], [], [], [], []
    current = {}
    for pid, s in series.items():
        feats, end = _windows(s, quiz.get(pid), w)
        if feats is None:
            continue
        current[pid] = feats[-1]
        ev = events.get(pid)
        keep = end < ev if ev else np.ones(len(end), bool)  # only data from before the event
        if not keep.any():
            continue
        X.append(feats[keep])
        y.append(((ev - end[keep]) <= t["horizon_days"]).astype(float) if ev else np.zeros(int(keep.sum())))
        owner.append(np.full(int(keep.sum()), pid))
        end_day.append(end[keep])
        row.append(np.nonzero(keep)[0])
    idx = {"as_of": as_of, "n_events": n_events, "series": series, "events": events, "current": current, "w": w}
    if X:
        X = np.vstack(X)
        idx["X"] = X
        idx["mu"], idx["sd"] = X.mean(axis=0), X.std(axis=0) + 1e-9
        idx["Xs"] = (X - idx["mu"]) / idx["sd"]
        idx["y"], idx["owner"] = np.concatenate(y), np.concatenate(owner)
        idx["end_day"], idx["row"] = np.concatenate(end_day), np.concatenate(row)
        # The most windows any one patient has: search that many extra so a patient's own windows can be dropped.
        idx["max_own"] = int(np.unique(idx["owner"], return_counts=True)[1].max())
        idx["nn"] = NearestNeighbors().fit(idx["Xs"])
    if use_cache:
        _cache.clear()  # keep only the latest index in memory
        _cache[key] = idx
    return idx


def _search(idx, k, xs, pids):
    """Nearest neighbours of each standardised query, skipping the query patient's own windows."""
    kq = min(len(idx["y"]), k + idx["max_own"])
    dist, nbr = idx["nn"].kneighbors(xs, n_neighbors=kq)
    out = []
    for d, n, pid in zip(dist, nbr, pids):
        mask = idx["owner"][n] != pid
        out.append((d[mask], n[mask]))
    return out


def _result(idx, cfg, pid, d, n):
    t = cfg["trajectory"]
    dk, nk = d[:t["k"]], n[:t["k"]]
    wts = 1.0 / (dk + 1e-6)
    share = float((wts * idx["y"][nk]).sum() / wts.sum())
    n_pos = int(idx["y"][nk].sum())
    peers, seen = [], set()
    for dd, j in zip(d, n):
        p = int(idx["owner"][j])
        if p in seen:
            continue
        seen.add(p)
        s, r, w = idx["series"][p], int(idx["row"][j]), idx["w"]
        positive = bool(idx["y"][j])
        peers.append({"peer": f"peer-{chr(65 + len(peers))}", "patient_id": p, "had_event_within_horizon": positive,
                      "distance": round(float(dd), 2), "sbp": s["sbp"][r:r + w].astype(int).tolist(),
                      "dates": s["dates"][r:r + w],
                      "days_to_event": int(idx["events"][p] - idx["end_day"][j]) if positive else None})
        if len(peers) >= t["show_peers"]:
            break
    flag = "yes" if share >= t["flag_share_gte"] else "no"
    reason = (f"{n_pos} of the {len(nk)} most similar local patient patterns were followed by a stroke or "
              f"admission within {t['horizon_days']} days (weighted share {share:.2f}).")
    return {"patient_id": pid, "share": round(share, 2), "flag": flag, "peers": peers, "reason_text": reason}


def _not_enough(pid, n_events, need):
    return {"patient_id": pid, "share": None, "flag": "no", "peers": [],
            "reason_text": f"Not enough local events yet ({n_events} of {need} needed)."}


def _write(con, as_of, results):
    con.executemany("DELETE FROM trajectory_scores WHERE patient_id = ? AND date = ?",
                    [(pid, as_of) for pid in results])
    con.executemany(
        "INSERT INTO trajectory_scores (patient_id, date, share_peers_with_event, peer_ids_json, flag, reason_text, "
        "model_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(pid, as_of, r["share"], dumps([p["patient_id"] for p in r["peers"]]), r["flag"], r["reason_text"],
          MODEL_VERSION) for pid, r in results.items()])
    con.commit()


# ---------- scoring ----------

def score_all(con, cfg, as_of=None, min_events=None, write=True):
    """Score every active patient's current window. Returns {patient_id: result}."""
    as_of = iso(as_of)
    need = cfg["trajectory"]["min_events"] if min_events is None else min_events
    idx = build_index(con, cfg, as_of, use_cache=True)
    active = {r[0] for r in con.execute("SELECT id FROM patients WHERE status = 'active'").fetchall()}
    pids = [p for p in idx["current"] if p in active]
    results = {}
    if idx["n_events"] < need or "nn" not in idx:
        results = {p: _not_enough(p, idx["n_events"], need) for p in pids}
    elif pids:
        xs = (np.array([idx["current"][p] for p in pids]) - idx["mu"]) / idx["sd"]
        for start in range(0, len(pids), 5000):  # in chunks, to keep memory small on one laptop
            part = pids[start:start + 5000]
            for p, (d, n) in zip(part, _search(idx, cfg["trajectory"]["k"], xs[start:start + 5000], part)):
                results[p] = _result(idx, cfg, p, d, n)
    if write:
        con.execute("DELETE FROM trajectory_scores WHERE date = ?", (as_of,))
        _write(con, as_of, results)
    return results


def score_one(con, cfg, patient_id, as_of=None, min_events=None, write=True):
    """Score one patient's fresh current window against the (cached) index, for example after an SMS."""
    as_of = iso(as_of)
    need = cfg["trajectory"]["min_events"] if min_events is None else min_events
    idx = build_index(con, cfg, as_of, use_cache=True)
    series, quiz, _ = _load(con, as_of, patient_id)
    feats = None
    if patient_id in series:
        feats, _ = _windows(series[patient_id], quiz.get(patient_id), idx["w"])
    if feats is None:
        return {"patient_id": patient_id, "share": None, "flag": "no", "peers": [],
                "reason_text": f"Fewer than {idx['w']} readings."}
    if idx["n_events"] < need or "nn" not in idx:
        res = _not_enough(patient_id, idx["n_events"], need)
    else:
        xs = ((feats[-1] - idx["mu"]) / idx["sd"])[None, :]
        d, n = _search(idx, cfg["trajectory"]["k"], xs, [patient_id])[0]
        res = _result(idx, cfg, patient_id, d, n)
    if write:
        _write(con, as_of, {patient_id: res})
    return res


def for_patient(con, cfg, patient_id, as_of=None, min_events=None):
    """One patient's result with anonymised peer BP series, for the frontend."""
    res = score_one(con, cfg, patient_id, as_of, min_events, write=False)
    # Peers by anonymised id only (PLAN.md 11b): drop the real id.
    res["peers"] = [{k: v for k, v in p.items() if k != "patient_id"} for p in res["peers"]]
    return res


def evaluate(con, cfg, as_of=None, min_events=None):
    """Leave one patient out: AUC on windows that end before any event, and share flagged by group."""
    from sklearn.metrics import roc_auc_score
    as_of = iso(as_of)
    t = cfg["trajectory"]
    need = t["min_events"] if min_events is None else min_events
    idx = build_index(con, cfg, as_of, use_cache=False)
    if idx["n_events"] < need or "nn" not in idx:
        return {"note": f"not enough local events ({idx['n_events']} of {need})"}
    owners, y = idx["owner"], idx["y"]
    scores = np.zeros(len(owners))
    for start in range(0, len(owners), 5000):
        part = slice(start, start + 5000)
        for i, (d, n) in enumerate(_search(idx, t["k"], idx["Xs"][part], owners[part])):
            dk, nk = d[:t["k"]], n[:t["k"]]
            wts = 1.0 / (dk + 1e-6)
            scores[start + i] = (wts * y[nk]).sum() / wts.sum()
    auc = roc_auc_score(y, scores) if len(set(y)) > 1 else None
    # One window per patient: the last one before the event, or the last one overall.
    last = {int(p): i for i, p in enumerate(owners)}
    flagged = {"event": [], "no_event": []}
    for p, i in last.items():
        flagged["event" if p in idx["events"] else "no_event"].append(bool(scores[i] >= t["flag_share_gte"]))
    share = lambda v: round(sum(v) / len(v), 2) if v else None  # noqa: E731
    flagged_w = scores >= t["flag_share_gte"]
    confusion = {"true_positive": int((flagged_w & (y == 1)).sum()), "false_positive": int((flagged_w & (y == 0)).sum()),
                 "false_negative": int((~flagged_w & (y == 1)).sum()), "true_negative": int((~flagged_w & (y == 0)).sum())}
    return {"windows": int(len(y)), "positive_windows": int(y.sum()), "events": idx["n_events"], "confusion": confusion,
            "auc_leave_one_patient_out": round(float(auc), 3) if auc is not None else None,
            "share_flagged_event_patients": share(flagged["event"]),
            "share_flagged_no_event_patients": share(flagged["no_event"]), "min_events_used": need}


def evaluate_external(train_con, test_con, cfg, as_of=None, min_events=None):
    """Independent test: the index is built from one database (development patients) and every historical
    window of another database (held-out patients) is scored against it. No patient is in both."""
    from sklearn.metrics import roc_auc_score
    t = cfg["trajectory"]
    need = t["min_events"] if min_events is None else min_events
    train = build_index(train_con, cfg, as_of, use_cache=False)
    test = build_index(test_con, cfg, as_of, use_cache=False)
    if train["n_events"] < need or "nn" not in train or "X" not in test:
        return {"note": f"not enough local events in the training set ({train['n_events']} of {need})"}
    xs = (test["X"] - train["mu"]) / train["sd"]
    dist, nbr = train["nn"].kneighbors(xs, n_neighbors=t["k"])
    wts = 1.0 / (dist + 1e-6)
    scores = (wts * train["y"][nbr]).sum(axis=1) / wts.sum(axis=1)
    y = test["y"]
    auc = roc_auc_score(y, scores) if len(set(y)) > 1 else None
    last = {int(p): i for i, p in enumerate(test["owner"])}
    flagged = {"event": [], "no_event": []}
    for p, i in last.items():
        flagged["event" if p in test["events"] else "no_event"].append(bool(scores[i] >= t["flag_share_gte"]))
    share = lambda v: round(sum(v) / len(v), 2) if v else None  # noqa: E731
    flagged_w = scores >= t["flag_share_gte"]
    confusion = {"true_positive": int((flagged_w & (y == 1)).sum()), "false_positive": int((flagged_w & (y == 0)).sum()),
                 "false_negative": int((~flagged_w & (y == 1)).sum()), "true_negative": int((~flagged_w & (y == 0)).sum())}
    return {"windows": int(len(y)), "positive_windows": int(y.sum()), "events": test["n_events"], "confusion": confusion,
            "training_events": train["n_events"],
            "auc_leave_one_patient_out": round(float(auc), 3) if auc is not None else None,
            "share_flagged_event_patients": share(flagged["event"]),
            "share_flagged_no_event_patients": share(flagged["no_event"]), "min_events_used": need}


def main():
    parser = argparse.ArgumentParser(description="Peer-trajectory scores for every patient.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--date", default=None)
    parser.add_argument("--min-events", type=int, default=None, help="override config.trajectory.min_events (demo)")
    parser.add_argument("--evaluate", action="store_true", help="print the leave-one-patient-out table")
    args = parser.parse_args()
    con = connect(args.db)
    cfg = load_config(args.config)
    res = score_all(con, cfg, args.date, args.min_events)
    flagged = [r for r in res.values() if r["flag"] == "yes"]
    print(f"Scored {len(res)} patients; {len(flagged)} flagged.")
    for r in sorted(flagged, key=lambda r: -r["share"])[:10]:
        print(f"  patient {r['patient_id']:>3}: {r['reason_text']} peers {[p['patient_id'] for p in r['peers']]}")
    if not flagged and res:
        print("  " + next(iter(res.values()))["reason_text"])
    if args.evaluate:
        print(evaluate(con, cfg, args.date, args.min_events))


if __name__ == "__main__":
    main()
