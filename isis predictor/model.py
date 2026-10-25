"""Linear regression: predict the colour of the 07:00 OURCs Isis flag from 07:00 weather and
the previous day's flag.

Follows the least squares method from Lecture 3 (Kanade): X has a leading column of 1s for
the bias w0, and w = (XᵀX)⁻¹Xᵀy is found by solving the normal equations XᵀX w = Xᵀy.

The colour is predicted one-vs-rest: for each colour k, y_k is 1 if the flag is that colour and
0 otherwise, and a separate weight vector w_k is fitted. The prediction is the colour whose
regression gives the highest score. (Numbering the colours 0, 1, 2, ... and fitting a single
regression would impose an unnatural structure, as the notes warn for days of the week.)

Discrete data is one-hot encoded with one category dropped, so the reference category is the
row of all 0s. This avoids the linear dependence that a full one-hot encoding creates with the
bias column. Discrete inputs:
  - the WMO weather code (reference: Clear sky, code 0)
  - the previous day's 07:00 flag (reference: Green)

weather_description is never used; it is just a text label for weather_code_WMO.

Uses only the Python standard library. Run with: python model.py
"""

from pathlib import Path
import csv
import math
from datetime import date, timedelta

DATA = Path(__file__).resolve().parent / "hectorcsv1.csv"
TRAIN_FRACTION = 0.8

NUMERIC = [
    "temperature_2m_C",
    "precipitation_mm_prev_hour",
    "wind_speed_10m_kmh",
    "wind_gusts_10m_kmh",
    "visibility_m",
    "cape_J_per_kg",
]

# Categories that are represented by all one-hot columns being 0.
REFERENCE_CODE = 0
REFERENCE_FLAG = "Green"

# Monitored flag colours, least to most severe (used to order the output).
COLOURS = ["Green", "Light Blue", "Dark Blue", "Amber", "Red", "Black"]


def load_rows():
    """Monitored (non-Grey) rows, each with prev_flag set to the previous calendar day's flag.

    The previous day may itself be Grey (e.g. the first day of term), which is a valid input.
    Rows whose previous day is missing from the data are dropped.
    """
    with open(DATA, newline="", encoding="utf-8-sig") as f:
        all_rows = list(csv.DictReader(f))
    flag_on = {r["date"]: r["ourcs_isis_flag_at_0700"] for r in all_rows}
    rows = []
    for r in all_rows:
        yesterday = (date.fromisoformat(r["date"]) - timedelta(days=1)).isoformat()
        if r["ourcs_isis_flag_at_0700"] != "Grey" and yesterday in flag_on:
            rows.append({**r, "prev_flag": flag_on[yesterday]})
    rows.sort(key=lambda r: r["date"])
    return rows


def weather_codes(rows):
    """Codes that get their own one-hot column: every code seen in training except the reference.

    A code with no training rows would give an all-zero column and make XᵀX singular, so codes
    unseen in training fall back to the reference (all 0s) at prediction time.
    """
    return sorted({int(r["weather_code_WMO"]) for r in rows} - {REFERENCE_CODE})


def previous_flags(rows):
    """Previous-day flags that get their own one-hot column, chosen the same way as weather_codes."""
    return sorted({r["prev_flag"] for r in rows} - {REFERENCE_FLAG})


def feature_names(codes, flags):
    return (["bias"] + NUMERIC + ["wind_dir_sin", "wind_dir_cos"]
            + [f"wmo_{code}" for code in codes] + [f"prev_{flag}" for flag in flags])


def features(row, codes, flags):
    """One row of X: the raw weather columns plus prev_flag. Never reads weather_description."""
    x = [1.0] + [float(row[name]) for name in NUMERIC]
    # Wind direction is circular (359° is next to 1°), so encode it as a point on a circle.
    direction = math.radians(float(row["wind_direction_10m_deg"]))
    x += [math.sin(direction), math.cos(direction)]
    code = int(row["weather_code_WMO"])
    x += [1.0 if code == c else 0.0 for c in codes]
    x += [1.0 if row["prev_flag"] == f else 0.0 for f in flags]
    return x


def solve(a, b):
    """Solve a·x = b by Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("XᵀX is singular: the feature columns are linearly dependent")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            factor = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def fit(xs, ys_by_class):
    """Least squares estimate for each class: solve XᵀX w_k = Xᵀy_k.

    XᵀX is the same for every class, so it is built once.
    """
    d = len(xs[0])
    xtx = [[sum(x[i] * x[j] for x in xs) for j in range(d)] for i in range(d)]
    weights = {}
    for k, ys in ys_by_class.items():
        xty = [sum(x[i] * y for x, y in zip(xs, ys)) for i in range(d)]
        weights[k] = solve(xtx, xty)
    return weights


def predict(w, x):
    return sum(wj * xj for wj, xj in zip(w, x))


def train():
    """Fit on the chronological training split. Returns (weights, codes, flags, rows, split)."""
    rows = load_rows()
    split = int(len(rows) * TRAIN_FRACTION)
    codes = weather_codes(rows[:split])
    flags = previous_flags(rows[:split])
    xs = [features(r, codes, flags) for r in rows[:split]]
    colours = sorted({r["ourcs_isis_flag_at_0700"] for r in rows[:split]}, key=COLOURS.index)
    ys_by_class = {
        k: [1.0 if r["ourcs_isis_flag_at_0700"] == k else 0.0 for r in rows[:split]]
        for k in colours
    }
    return fit(xs, ys_by_class), codes, flags, rows, split


def predict_flag(weights, codes, flags, weather):
    """Production entry point: weather is a dict of the raw weather columns for one 07:00 reading,
    plus prev_flag (yesterday's 07:00 flag, e.g. "Green", "Red", "Grey").

    Returns (colour, scores by colour). weather_description is not needed and is ignored.
    """
    x = features(weather, codes, flags)
    scores = {k: predict(w, x) for k, w in weights.items()}
    return max(scores, key=scores.get), scores


def main():
    weights, codes, flags, rows, split = train()
    names = feature_names(codes, flags)
    colours = list(weights)

    train_rows, test = rows[:split], rows[split:]
    actual = [r["ourcs_isis_flag_at_0700"] for r in test]
    predicted = [predict_flag(weights, codes, flags, r)[0] for r in test]
    correct = sum(1 for a, p in zip(actual, predicted) if a == p)
    same_as_yesterday = sum(1 for r, a in zip(test, actual) if r["prev_flag"] == a)
    majority = max(colours, key=lambda k: sum(r["ourcs_isis_flag_at_0700"] == k
                                              for r in train_rows))
    majority_correct = sum(1 for a in actual if a == majority)

    print(f"Rows (Grey excluded): {len(rows)}")
    print(f"Train: {split} rows, {rows[0]['date']} to {rows[split - 1]['date']}")
    print(f"Test:  {len(test)} rows, {test[0]['date']} to {test[-1]['date']}")
    print()
    print(f"Test accuracy:                     {correct / len(test):.1%}")
    print(f"Baseline, same colour as yesterday: {same_as_yesterday / len(test):.1%}")
    print(f"Baseline, always {majority} (train majority): {majority_correct / len(test):.1%}")
    print()
    print("Per colour (test):")
    print(f"  {'colour':12s} {'count':>5s} {'precision':>10s} {'recall':>8s}")
    for k in colours:
        tp = sum(1 for a, p in zip(actual, predicted) if a == k and p == k)
        n_pred = predicted.count(k)
        n_act = actual.count(k)
        prec = f"{tp / n_pred:.1%}" if n_pred else "n/a"
        rec = f"{tp / n_act:.1%}" if n_act else "n/a"
        print(f"  {k:12s} {n_act:5d} {prec:>10s} {rec:>8s}")
    print()
    print("Confusion matrix (rows = actual, cols = predicted):")
    print("  " + " " * 12 + "".join(f"{k:>11s}" for k in colours))
    for a_k in colours:
        counts = [sum(1 for a, p in zip(actual, predicted) if a == a_k and p == p_k)
                  for p_k in colours]
        print(f"  {a_k:12s}" + "".join(f"{c:11d}" for c in counts))
    print()
    print("Misclassified test days:")
    for r, a, p in zip(test, actual, predicted):
        if a != p:
            print(f"  {r['date']}  yesterday {r['prev_flag']:10s} actual {a:10s} predicted {p}")
    print()
    print(f"Weights (raw units; + pushes towards that colour; wmo_* relative to code "
          f"{REFERENCE_CODE}, Clear sky; prev_* relative to previous day {REFERENCE_FLAG}):")
    print(f"  {'feature':28s}" + "".join(f"{k:>11s}" for k in colours))
    for j, name in enumerate(names):
        print(f"  {name:28s}" + "".join(f"{weights[k][j]:+11.5f}" for k in colours))


if __name__ == "__main__":
    main()
