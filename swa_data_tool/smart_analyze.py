"""Automatic, diverse analysis of a cleaned dataset - every number re-checked by an independent calculation."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .checks import Check, close
from .smart_clean import Col, Dataset

MAX_LEVELS = 30


# ------------------------------------------------------------------ independent (plain Python) maths
def py_mean(xs):
    return math.fsum(xs) / len(xs)


def py_percentile(xs, p):
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def py_std(xs):
    if len(xs) < 2:
        return None
    m = py_mean(xs)
    return math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def py_pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = py_mean(xs), py_mean(ys)
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def py_slope(xs, ys):
    mx, my = py_mean(xs), py_mean(ys)
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


# ------------------------------------------------------------------------------------ formatting
def fmt_num(col: Col, v, extra_dec=0) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    dec = min(max(col.decimals, 2) if extra_dec else col.decimals, 4)      # averages show 2 decimals
    s = f"{v:,.{dec}f}"
    return f"{col.prefix}{s}" if col.prefix and not s.startswith("-") else (f"-{col.prefix}{s[1:]}" if col.prefix else s)


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


@dataclass
class Analysis:
    overview: dict = field(default_factory=dict)
    profile: list = field(default_factory=list)
    frequencies: dict = field(default_factory=dict)       # col -> list[(value, count, pct)]
    stats: dict = field(default_factory=dict)             # col -> dict
    groups: list = field(default_factory=list)            # [{measure, by, rows:[...], ...}]
    correlations: list = field(default_factory=list)      # [{a,b,r,n,label}]
    trends: list = field(default_factory=list)
    outliers: list = field(default_factory=list)
    insights: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _label(r):
    a = abs(r)
    return "strong" if a >= 0.7 else "moderate" if a >= 0.4 else "weak" if a >= 0.2 else "negligible"


def analyze(ds: Dataset) -> Analysis:
    df, cols = ds.df, ds.cols
    n = len(df)
    A = Analysis()
    visible = [c for c in cols if not c.pii]
    cats = [c for c in visible if c.role in ("category", "boolean")]
    meas = [c for c in visible if c.role == "measure"]
    years = [c for c in visible if c.role == "year"]
    dates = [c for c in visible if c.role == "date"]
    missing_cells = int(df.isna().sum().sum())
    A.overview = {"rows": n, "columns": len(cols), "missing_cells": missing_cells,
                  "missing_pct": (missing_cells / (n * len(cols)) if n and cols else 0.0),
                  "duplicates": int(df[[c.name for c in cols if c.derived_from is None]].duplicated().sum()),
                  "roles": dict(Counter(c.role for c in cols))}

    # ---- profile
    for c in cols:
        s = df[c.name]
        nm = int(s.isna().sum())
        distinct = int(s.nunique(dropna=True))
        if c.pii:
            detail = "(personal data - hidden)"
        elif c.role in ("measure", "year"):
            detail = f"{fmt_num(c, float(s.min()))} to {fmt_num(c, float(s.max()))}" if nm < n else ""
            if c.role == "year":
                detail = f"{int(s.min())} to {int(s.max())}" if nm < n else ""
        elif c.role == "date":
            detail = f"{s.min():%Y-%m-%d} to {s.max():%Y-%m-%d}" if nm < n else ""
        elif c.role in ("category", "boolean"):
            vc = s.value_counts()
            detail = f"most common: {vc.index[0]} ({vc.iloc[0]})" if len(vc) else ""
        else:
            detail = ""
        A.profile.append([c.name, c.role, n - nm, nm, (nm / n if n else 0.0), distinct, detail])

    # ---- frequencies (verified)
    for c in cats:
        s = df[c.name]
        distinct = s.nunique(dropna=True)
        if distinct > MAX_LEVELS or distinct < 1:
            continue
        vc = s.value_counts(dropna=True)
        rows = sorted(((str(k), int(v)) for k, v in vc.items()), key=lambda kv: (-kv[1], kv[0].casefold()))
        py = Counter(v for v in s.tolist() if isinstance(v, str))
        py_rows = sorted(py.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
        A.checks.append(Check(f"Frequencies of '{c.name}' agree between two calculations", f"{len(py_rows)} values", f"{len(rows)} values", rows == py_rows))
        A.frequencies[c.name] = [(k, v, v / n if n else 0.0) for k, v in rows]

    # ---- measure statistics (verified)
    for c in meas:
        s = df[c.name].dropna()
        if len(s) == 0:
            continue
        xs = [float(x) for x in s]
        st = {"n": len(xs), "missing": n - len(xs), "mean": float(np.mean(xs)), "median": float(np.median(xs)),
              "std": float(np.std(xs, ddof=1)) if len(xs) > 1 else None, "min": min(xs), "max": max(xs),
              "q1": float(np.percentile(xs, 25)), "q3": float(np.percentile(xs, 75)), "sum": math.fsum(xs)}
        ok = (close(st["mean"], py_mean(xs)) and close(st["q1"], py_percentile(xs, 25)) and close(st["q3"], py_percentile(xs, 75))
              and close(st["median"], py_percentile(xs, 50)) and close(st["std"], py_std(xs)) and close(st["sum"], math.fsum(xs)))
        A.checks.append(Check(f"Statistics of '{c.name}' (mean, median, quartiles, spread) agree between two calculations", "equal", "equal" if ok else "DIFFERENT", ok))
        A.stats[c.name] = st
        # outliers by the 1.5 x IQR rule
        iqr = st["q3"] - st["q1"]
        if len(xs) >= 8 and iqr > 0:
            lo, hi = st["q1"] - 1.5 * iqr, st["q3"] + 1.5 * iqr
            for idx, v in s.items():
                if v < lo or v > hi:
                    A.outliers.append({"column": c.name, "sheet_row": ds.row_numbers[idx], "value": float(v), "low": lo, "high": hi,
                                       "side": "above" if v > hi else "below"})

    # ---- group comparisons (verified)
    cand = []
    key = ("price", "cost", "revenue", "sales", "amount", "income", "salary", "value", "total", "fee")
    meas = sorted(meas, key=lambda c: (0 if any(k in c.name.lower() for k in key) else 1))
    for m in meas[:4]:
        for c in cats:
            lv = df[c.name].nunique(dropna=True)
            if 2 <= lv <= 12 and n / lv >= 2:            # groups of about 2+ rows, otherwise averages are meaningless
                g = df.groupby(c.name)[m.name].agg(["count", "mean", "median", "min", "max", "sum"])
                g = g[g["count"] > 0]
                if len(g) < 2:
                    continue
                overall = df[m.name].mean()
                spread = (g["mean"].max() - g["mean"].min()) / abs(overall) if overall else 0.0
                cand.append((spread, m, c, g))
    cand.sort(key=lambda t: -t[0])
    for spread, m, c, g in cand[:6]:
        rows = []
        py_ok = True
        for level, r in g.sort_values("mean", ascending=False).iterrows():
            vals = [float(x) for x, lv in zip(df[m.name], df[c.name]) if lv == level and not pd.isna(x)]
            if not (close(float(r["mean"]), py_mean(vals)) and int(r["count"]) == len(vals) and close(float(r["sum"]), math.fsum(vals))):
                py_ok = False
            rows.append({"level": str(level), "n": int(r["count"]), "mean": float(r["mean"]), "median": float(r["median"]),
                         "min": float(r["min"]), "max": float(r["max"]), "sum": float(r["sum"])})
        A.checks.append(Check(f"Average '{m.name}' by '{c.name}' agrees between two calculations", "equal", "equal" if py_ok else "DIFFERENT", py_ok))
        A.groups.append({"measure": m.name, "by": c.name, "rows": rows, "spread": spread})

    # ---- correlations (verified)
    numeric = meas + years
    for i in range(len(numeric)):
        for j in range(i + 1, len(numeric)):
            a, b = numeric[i], numeric[j]
            sub = df[[a.name, b.name]].dropna()
            if len(sub) < 3:
                continue
            xs, ys = [float(x) for x in sub[a.name]], [float(y) for y in sub[b.name]]
            r = py_pearson(xs, ys)
            if r is None:
                continue
            r_np = float(np.corrcoef(xs, ys)[0, 1])
            A.checks.append(Check(f"Correlation of '{a.name}' and '{b.name}' agrees between two calculations", f"{r:.6f}", f"{r_np:.6f}", close(r, r_np, rel=1e-9, abs_=1e-9)))
            A.correlations.append({"a": a.name, "b": b.name, "r": r, "n": len(xs), "label": _label(r),
                                   "reliable": len(xs) >= 10})
    A.correlations.sort(key=lambda d: -abs(d["r"]))

    # ---- trends over year / date
    for yc in years:
        counts = df.groupby(yc.name).size().sort_index()
        row = {"by": yc.name, "periods": [int(k) for k in counts.index], "counts": [int(v) for v in counts.values], "measures": {}}
        for m in meas[:2]:
            sub = df[[yc.name, m.name]].dropna()
            if len(sub) >= 5 and sub[yc.name].nunique() >= 3:
                means = sub.groupby(yc.name)[m.name].mean()
                xs, ys = [float(x) for x in sub[yc.name]], [float(y) for y in sub[m.name]]
                slope = py_slope(xs, ys)
                slope_np = float(np.polyfit(xs, ys, 1)[0]) if len(set(xs)) > 1 else None
                ok = slope is not None and slope_np is not None and close(slope, slope_np, rel=1e-7, abs_=1e-7)
                A.checks.append(Check(f"Trend of '{m.name}' over '{yc.name}' agrees between two calculations", f"{slope:.6f}" if slope is not None else "-", f"{slope_np:.6f}" if slope_np is not None else "-", ok))
                row["measures"][m.name] = {"means": {int(k): float(v) for k, v in means.items()}, "slope": slope,
                                           "r": py_pearson(xs, ys), "n": len(xs)}
        A.trends.append(row)
    for dc in dates:
        s = df[dc.name].dropna()
        if len(s) >= 5:
            per = s.dt.to_period("M").astype(str)
            vc = per.value_counts().sort_index()
            A.trends.append({"by": dc.name, "periods": list(vc.index), "counts": [int(v) for v in vc.values], "measures": {}, "monthly": True})

    # ---- plain-language insights
    ins = A.insights
    for c in cats[:5]:
        f = A.frequencies.get(c.name)
        if not f:
            continue
        top = max(v for _, v, _ in f)
        winners = [k for k, v, _ in f if v == top]
        low = min(v for _, v, _ in f)
        losers = [k for k, v, _ in f if v == low]
        s = (f"Most common {c.name}: {', '.join(winners[:3])} - {top} of {n} rows ({top / n:.1%})" if len(winners) == 1 else
             f"{c.name} is tied for most common: {', '.join(winners[:4])} ({top} rows each)")
        if len(f) > 1:
            s += (f". Least common: {', '.join(losers[:3])} ({low} row{'s' if low != 1 else ''})" + (" each" if len(losers) > 1 else ""))
        ins.append(s + ".")
    for c in meas[:4]:
        st = A.stats.get(c.name)
        if st:
            ins.append(f"{c.name}: average {fmt_num(c, st['mean'], 1)}, median {fmt_num(c, st['median'], 1)}, "
                       f"from {fmt_num(c, st['min'])} to {fmt_num(c, st['max'])} ({st['n']} values" + (f", {st['missing']} missing" if st['missing'] else "") + ").")
    for g in A.groups[:3]:
        m = ds.col(g["measure"])
        hi, lo = g["rows"][0], g["rows"][-1]
        s = f"Average {g['measure']} is highest for {g['by']} = {hi['level']} ({fmt_num(m, hi['mean'])}, n={hi['n']}) and lowest for {lo['level']} ({fmt_num(m, lo['mean'])}, n={lo['n']})."
        if min(hi["n"], lo["n"]) < 5:
            s += " Some groups are small (fewer than 5 rows), so treat this with caution."
        ins.append(s)
    for d in A.correlations[:4]:
        if d["label"] != "negligible":
            direction = "rises" if d["r"] > 0 else "falls"
            yearlike = {c.name for c in years}
            if d["b"] in yearlike or d["a"] in yearlike:
                other, yr = (d["a"], d["b"]) if d["b"] in yearlike else (d["b"], d["a"])
                s = (f"{other} tends to be {'higher' if d['r'] > 0 else 'lower'} in later {yr} values - a {d['label']} {'positive' if d['r'] > 0 else 'negative'} relationship "
                     f"(r = {d['r']:.2f}, {d['n']} rows). This is a pattern, not proof of cause.")
            else:
                s = (f"{d['a']} and {d['b']} show a {d['label']} {'positive' if d['r'] > 0 else 'negative'} relationship (r = {d['r']:.2f}, {d['n']} rows): "
                     f"when {d['a']} goes up, {d['b']} usually {direction}. This is a pattern, not proof of cause.")
            if not d["reliable"]:
                s += " There are fewer than 10 rows, so this is not reliable yet."
            ins.append(s)
    for t in A.trends:
        for mname, md in t["measures"].items():
            if md["slope"] is not None and md["r"] is not None and abs(md["r"]) >= 0.3:
                m = ds.col(mname)
                ins.append(f"Over {t['by']}, {mname} changes by about {fmt_num(m, md['slope'], 1)} per year on average (r = {md['r']:.2f}).")
    if A.outliers:
        byc = Counter(o["column"] for o in A.outliers)
        ins.append("Unusual values (1.5 x IQR rule): " + ", ".join(f"{k}: {v}" for k, v in byc.items()) + ". They are kept in the analysis - check them in the 'Outliers' sheet.")
    for p in A.profile:
        if p[4] >= 0.1 and p[3] > 0:
            ins.append(f"{p[0]} is missing in {p[4]:.0%} of rows ({p[3]} of {n}).")
    return A
