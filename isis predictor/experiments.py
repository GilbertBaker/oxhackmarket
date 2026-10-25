"""Evaluate the flag forecast at each horizon with expanding-window time-series cross-validation.

Each fold trains on earlier days and tests on the next block. Training days whose
targets fall inside the test block are dropped (a purge gap of HORIZONS days), so
no test-period flag is ever used for training. Writes model/evaluation.json for
the website.

Run: py experiments.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from features import CLASSES, HORIZONS, build_dataset, check_no_lookahead, load_data
from neuralnet import FEATURES, fit_ensemble

ROOT = Path(__file__).resolve().parent
EVALUATION_FILE = ROOT / "model" / "evaluation.json"
FOLDS = 5
FIRST_TRAIN_FRACTION = 0.3  # The first fold needs enough history to see a range of river conditions.
SEEDS = [0, 1, 2]
ENSEMBLE = 5
GREEN = CLASSES.index("Green")
CALIBRATION_BINS = np.linspace(0, 1, 6)


def folds(dates):
    """Expanding window with a purge: train only on days whose targets end before the test block."""
    n = len(dates)
    edges = np.linspace(int(n * FIRST_TRAIN_FRACTION), n, FOLDS + 1).astype(int)
    for i in range(FOLDS):
        test_start = dates[edges[i]]
        train = np.flatnonzero(dates + pd.Timedelta(days=HORIZONS) < test_start)
        yield train, np.arange(edges[i], edges[i + 1])


def score(pred, y, today):
    known = y >= 0
    pred, y, today = pred[known], y[known], today[known]
    change = y != today
    return {
        "days": int(len(y)),
        "exact": float((pred == y).mean()),
        "green_or_not": float(((pred == GREEN) == (y == GREEN)).mean()),
        "change_days": int(change.sum()),
        "change_days_right": int((pred[change] == y[change]).sum()),
        "false_changes": int((pred[~change] != y[~change]).sum()),
    }


def combine(scores):
    """Pool fold scores: day-weighted accuracies, summed counts (averaged over seeds)."""
    days = sum(s["days"] for s in scores)
    runs = len(scores) // FOLDS
    out = {k: sum(s[k] * s["days"] for s in scores) / days for k in ("exact", "green_or_not")}
    out.update({k: sum(s[k] for s in scores) / runs for k in ("days", "change_days", "change_days_right", "false_changes")})
    return out


def main():
    flags, ea, fc = load_data()
    check_no_lookahead(flags, ea, fc, n_checks=100)
    print("No-lookahead check passed")
    X, Y, today, dates, _ = build_dataset(flags, ea, fc, FEATURES)
    test_start = dates[next(folds(dates))[1][0]]
    print(f"{len(X)} days, testing {test_start.date()} to {dates[-1].date()}, "
          f"{FOLDS} folds x {len(SEEDS)} seeds, ensemble of {ENSEMBLE}\n")

    # Copying today's flag (Green after a Grey day, the usual start-of-term flag).
    copy = np.where(today < 0, GREEN, today)
    baseline = [[score(copy[te], Y[te, h], today[te]) for _, te in folds(dates)] for h in range(HORIZONS)]
    model = [[] for _ in range(HORIZONS)]
    p_green, green_actual = [], []
    for seed in SEEDS:
        for tr, te in folds(dates):
            probs = fit_ensemble(X[tr], Y[tr], size=ENSEMBLE, seed=seed * 100)(X[te])
            for h in range(HORIZONS):
                model[h].append(score(probs[:, h].argmax(axis=1), Y[te, h], today[te]))
            known = Y[te] >= 0
            p_green.append(probs[..., GREEN][known])
            green_actual.append((Y[te] == GREEN)[known])

    results = []
    print(f"{'days ahead':>10}  {'':8}  exact  green/not  change days right  false changes")
    for h in range(HORIZONS):
        b, m = combine(baseline[h]), combine(model[h])
        results.append({"horizon_days": h + 1, "copy_today": b, "model": m})
        for name, r in (("copy", b), ("model", m)):
            print(f"{h + 1:>10}  {name:8}  {r['exact']:.3f}  {r['green_or_not']:.3f}      "
                  f"{r['change_days_right']:5.1f}/{r['change_days']:<5.0f}      {r['false_changes']:5.1f}")

    # Calibration: when the model says P(green) = x, how often is it green?
    p, actual = np.concatenate(p_green), np.concatenate(green_actual)
    bins = np.clip(np.digitize(p, CALIBRATION_BINS[1:-1]), 0, len(CALIBRATION_BINS) - 2)
    calibration = [
        {"from": float(CALIBRATION_BINS[i]), "to": float(CALIBRATION_BINS[i + 1]),
         "predictions": int((bins == i).sum()),
         "mean_p_green": float(p[bins == i].mean()) if (bins == i).any() else None,
         "observed_green": float(actual[bins == i].mean()) if (bins == i).any() else None}
        for i in range(len(CALIBRATION_BINS) - 1)
    ]
    print("\nP(green) calibration (all horizons):")
    for c in calibration:
        if c["predictions"]:
            print(f"  {c['from']:.1f}-{c['to']:.1f}: {c['predictions']:5d} predictions, "
                  f"mean P {c['mean_p_green']:.2f}, actually green {c['observed_green']:.2f}")

    EVALUATION_FILE.parent.mkdir(exist_ok=True)
    EVALUATION_FILE.write_text(json.dumps({
        "tested_from": str(test_start.date()),
        "tested_to": str(dates[-1].date()),
        "method": f"expanding-window cross-validation, {FOLDS} folds, {HORIZONS}-day purge gap, "
                  f"{len(SEEDS)} seeds, ensemble of {ENSEMBLE}",
        "horizons": results,
        "calibration": calibration,
    }, indent=2))
    print(f"\nSaved {EVALUATION_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
