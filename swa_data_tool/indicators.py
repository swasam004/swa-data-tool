"""Programme indicators with disaggregation (by sex, age group, location, month ...).

Each indicator is computed TWICE (pandas masks vs plain-Python loops) and the
two results must agree exactly - the same safeguard used for pivots.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .pivots import is_present
from .util import natural_key

TOTAL_LABEL = "All analysed records"


@dataclass
class IndicatorRow:
    group: str
    n: float | None            # count / distinct / records with value / numerator
    d: float | None            # denominator (percent only)
    value: float | None        # the indicator value (percent as a fraction 0..1)


@dataclass
class IndicatorResult:
    spec: dict
    sections: list = field(default_factory=list)    # [(dimension|None, [IndicatorRow])]
    headers: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    unique: bool = False                            # counts distinct people, not records

    @property
    def additive(self) -> bool:
        return (not self.unique) and self.spec["type"] in ("count", "sum", "percent")


def _fmt_filter(f: dict) -> str:
    op = {"equals": "=", "not_equals": "≠", "in": "in", "not_in": "not in", "gte": "≥", "gt": ">", "lte": "≤", "lt": "<"}[f["op"]]
    v = f["value"]
    if isinstance(v, list):
        v = "[" + ", ".join(f"{x:g}" if isinstance(x, float) else str(x) for x in v) + "]"
    elif isinstance(v, float):
        v = f"{v:g}"
    return f"{f['column']} {op} {v}"


def describe(spec: dict) -> str:
    t = spec["type"]
    cond = " AND ".join(_fmt_filter(f) for f in spec["where"])
    suffix = f" where {cond}" if cond else ""
    if t == "count":
        txt = f"Number of records{suffix}"
    elif t == "count_unique":
        txt = f"Number of distinct '{spec['values']}' among records{suffix}"
    elif t == "sum":
        txt = f"Sum of '{spec['values']}' among records{suffix}"
    elif t == "mean":
        txt = f"Average of '{spec['values']}' among records{suffix}"
    else:
        among = " AND ".join(_fmt_filter(f) for f in spec["among"])
        who = f"distinct '{spec['unique_by']}'" if spec["unique_by"] else "records"
        txt = f"Percent of {who}{suffix}, out of all {who}" + (f" where {among}" if among else "")
    if spec["disaggregate_by"]:
        txt += ". Disaggregated by " + ", ".join(spec["disaggregate_by"])
    return txt


def headers_for(spec: dict) -> list[str]:
    t = spec["type"]
    return {"count": ["Group", "Records"], "count_unique": ["Group", "Distinct"],
            "sum": ["Group", "Records with a value", "Sum"], "mean": ["Group", "Records with a value", "Average"],
            "percent": ["Group", "Numerator", "Denominator", "Percent"]}[t]


# ----------------------------------------------------------------------------- pandas
def _mask(df: pd.DataFrame, filters: list) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for f in filters:
        col, op, v = df[f["column"]], f["op"], f["value"]
        numeric = pd.api.types.is_numeric_dtype(col)
        ok = col.notna() if numeric else pd.Series(True, index=df.index)
        if op == "equals":
            part = ok & (col == v)
        elif op == "not_equals":
            part = ok & (col != v)
        elif op == "in":
            part = ok & col.isin(v)
        elif op == "not_in":
            part = ok & ~col.isin(v)
        elif op == "gte":
            part = ok & (col >= v)
        elif op == "gt":
            part = ok & (col > v)
        elif op == "lte":
            part = ok & (col <= v)
        else:
            part = ok & (col < v)
        m = m & part
    return m


def _distinct(series: pd.Series) -> float:
    return float(len({v for v in series.tolist() if is_present(v)}))


def _pd_row(df, spec, group_mask) -> tuple:
    t = spec["type"]
    base = group_mask
    if t == "percent":
        among = base & _mask(df, spec["among"])
        hit = among & _mask(df, spec["where"])
        if spec["unique_by"]:
            return _distinct(df.loc[hit, spec["unique_by"]]), _distinct(df.loc[among, spec["unique_by"]])
        return float(hit.sum()), float(among.sum())
    sel = base & _mask(df, spec["where"])
    if t == "count":
        return float(sel.sum()), None
    if t == "count_unique":
        return _distinct(df.loc[sel, spec["values"]]), None
    vals = df.loc[sel, spec["values"]].dropna()
    return float(len(vals)), float(vals.sum()) if t == "sum" else (float(vals.mean()) if len(vals) else None)


def _groups(df, dim):
    return sorted(df[dim].unique().tolist(), key=natural_key)


def compute_indicator(df: pd.DataFrame, spec: dict) -> IndicatorResult:
    res = IndicatorResult(spec=spec, headers=headers_for(spec),
                          unique=bool(spec["type"] == "count_unique" or spec.get("unique_by")))
    plan = [(None, [(TOTAL_LABEL, pd.Series(True, index=df.index))])]
    for dim in spec["disaggregate_by"]:
        plan.append((dim, [(g, df[dim] == g) for g in _groups(df, dim)]))
    for dim, groups in plan:
        rows = []
        for label, gm in groups:
            a, b = _pd_row(df, spec, gm)
            if spec["type"] == "percent":
                rows.append(IndicatorRow(label, a, b, (a / b) if b else None))
            elif spec["type"] in ("sum", "mean"):
                rows.append(IndicatorRow(label, a, None, b))
            else:
                rows.append(IndicatorRow(label, a, None, a))
        res.sections.append((dim, rows))
    res.warnings = filter_warnings(df, spec)
    return res


def filter_warnings(df: pd.DataFrame, spec: dict) -> list:
    out = []
    for f in spec["where"] + spec["among"]:
        col = df[f["column"]]
        if pd.api.types.is_numeric_dtype(col) or len(df) == 0:
            continue
        targets = f["value"] if isinstance(f["value"], list) else [f["value"]]
        present = set(col.unique().tolist())
        for tv in targets:
            if tv not in present:
                out.append(f"Indicator '{spec['name']}': the value '{tv}' never appears in column '{f['column']}' "
                           f"(check spelling). Values found: {sorted(map(str, present))[:12]}")
    return out


# ----------------------------------------------------------------- independent (plain Python)
def _py_pass(rec: dict, f: dict, numeric: bool) -> bool:
    v, op, t = rec[f["column"]], f["op"], f["value"]
    if numeric and (v is None or v != v):
        return False
    if op == "equals":
        return v == t
    if op == "not_equals":
        return v != t
    if op == "in":
        return v in t
    if op == "not_in":
        return v not in t
    if op == "gte":
        return v >= t
    if op == "gt":
        return v > t
    if op == "lte":
        return v <= t
    return v < t


def independent_indicator(df: pd.DataFrame, spec: dict) -> IndicatorResult:
    need = {f["column"] for f in spec["where"] + spec["among"]} | set(spec["disaggregate_by"])
    for k in ("values", "unique_by"):
        if spec.get(k):
            need.add(spec[k])
    need = sorted(need)
    numeric = {c: pd.api.types.is_numeric_dtype(df[c]) for c in need}
    cols = {c: df[c].tolist() for c in need}
    recs = [{c: cols[c][i] for c in need} for i in range(len(df))]
    ok = lambda r, fl: all(_py_pass(r, f, numeric[f["column"]]) for f in fl)   # noqa: E731
    t = spec["type"]
    res = IndicatorResult(spec=spec, headers=headers_for(spec),
                          unique=bool(t == "count_unique" or spec.get("unique_by")))
    plan = [(None, [(TOTAL_LABEL, lambda r: True)])]
    for dim in spec["disaggregate_by"]:
        plan.append((dim, [(g, (lambda r, g=g, dim=dim: r[dim] == g)) for g in sorted(set(cols[dim]), key=natural_key)]))
    for dim, groups in plan:
        rows = []
        for label, gtest in groups:
            members = [r for r in recs if gtest(r)]
            if t == "percent":
                among = [r for r in members if ok(r, spec["among"])]
                hit = [r for r in among if ok(r, spec["where"])]
                if spec["unique_by"]:
                    a = float(len({r[spec["unique_by"]] for r in hit if is_present(r[spec["unique_by"]])}))
                    b = float(len({r[spec["unique_by"]] for r in among if is_present(r[spec["unique_by"]])}))
                else:
                    a, b = float(len(hit)), float(len(among))
                rows.append(IndicatorRow(label, a, b, (a / b) if b else None))
                continue
            sel = [r for r in members if ok(r, spec["where"])]
            if t == "count":
                rows.append(IndicatorRow(label, float(len(sel)), None, float(len(sel))))
            elif t == "count_unique":
                n = float(len({r[spec["values"]] for r in sel if is_present(r[spec["values"]])}))
                rows.append(IndicatorRow(label, n, None, n))
            else:
                vals = [r[spec["values"]] for r in sel if not (r[spec["values"]] is None or r[spec["values"]] != r[spec["values"]])]
                n = float(len(vals))
                if t == "sum":
                    rows.append(IndicatorRow(label, n, None, math.fsum(vals)))
                else:
                    rows.append(IndicatorRow(label, n, None, (math.fsum(vals) / len(vals)) if vals else None))
        res.sections.append((dim, rows))
    return res
