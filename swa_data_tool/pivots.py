"""Pivot tables, computed TWICE by independent code paths.

1. compute_pivot()      - pandas groupby (fast)
2. independent_pivot()  - plain Python loops with math.fsum (exact summation)

checks.py compares every cell. A disagreement fails the run.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import ADDITIVE_AGGS
from .util import MISSING_LABEL, natural_key

AGG_TITLES = {"sum": "Sum of", "mean": "Average of", "count": "Count of", "min": "Minimum of",
              "max": "Maximum of", "median": "Median of", "count_unique": "Distinct count of"}


@dataclass
class PivotResult:
    spec: dict
    row_labels: list
    col_labels: list                      # [] when there is no column field
    matrix: list                          # rows x cols (or rows x 1 when no column field)
    row_totals: list
    col_totals: list
    grand_total: object
    label: str
    additive: bool
    rows_covered: int = 0
    decimals: int = 2

    @property
    def has_cols(self) -> bool:
        return bool(self.spec.get("columns"))


def pivot_label(spec: dict) -> str:
    if spec["agg"] == "count_rows":
        return "Number of records"
    return f"{AGG_TITLES[spec['agg']]} {spec['values']}"


def _decimals_for(spec: dict, cfg: dict) -> int:
    if spec["agg"] in ("count", "count_rows", "count_unique"):
        return 0
    base = cfg["spec_by_name"][spec["values"]]["decimals"]
    return max(base, 2) if spec["agg"] in ("mean", "median") else base


def _clean(v):
    if v is None:
        return None
    try:
        if math.isnan(v):
            return None
    except TypeError:
        pass
    return float(v)


def _order(labels, totals_by_label, sort):
    labels = list(labels)
    if sort == "label":
        return sorted(labels, key=natural_key)
    sign = -1 if sort == "value_desc" else 1
    def key(lbl):
        t = totals_by_label.get(lbl)
        return (t is None, sign * (t if t is not None else 0.0), natural_key(lbl))
    return sorted(labels, key=key)


def _finish(spec, cfg, rows_present, cols_present, cell, rtot, ctot, grand, covered):
    agg = spec["agg"]
    zero = 0.0 if agg in ADDITIVE_AGGS or agg == "count_unique" else None
    row_labels = _order(rows_present, rtot, spec["sort"])
    col_labels = sorted(cols_present, key=natural_key) if spec.get("columns") else []
    if col_labels:
        matrix = [[_clean(cell.get((r, c), zero)) for c in col_labels] for r in row_labels]
        col_totals = [_clean(ctot.get(c, zero)) for c in col_labels]
    else:
        matrix = [[_clean(cell.get((r, None), zero))] for r in row_labels]
        col_totals = [_clean(grand)]
    return PivotResult(
        spec=spec, row_labels=row_labels, col_labels=col_labels, matrix=matrix,
        row_totals=[_clean(rtot.get(r, zero)) for r in row_labels], col_totals=col_totals,
        grand_total=_clean(grand), label=pivot_label(spec), additive=agg in ADDITIVE_AGGS,
        rows_covered=covered, decimals=_decimals_for(spec, cfg),
    )


def is_present(v) -> bool:
    """A value counts as present unless it is blank/NaN/None or the '(Missing)' label."""
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return not (isinstance(v, str) and v in ("", MISSING_LABEL))


def prepare_values(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """For count / count_unique on text-like columns, turn blank & '(Missing)' into real missing values."""
    val = spec.get("values")
    if spec["agg"] in ("count", "count_unique") and val and not pd.api.types.is_numeric_dtype(df[val]):
        work = df.copy()
        work[val] = work[val].map(lambda v: v if is_present(v) else np.nan).astype(object)
        return work
    return df


def _pd_agg(groups, agg, val):
    if agg == "count_rows":
        return groups.size()
    s = groups[val]
    return {"sum": s.sum, "mean": s.mean, "count": s.count, "min": s.min, "max": s.max,
            "median": s.median, "count_unique": s.nunique}[agg]()


def compute_pivot(df: pd.DataFrame, spec: dict, cfg: dict) -> PivotResult:
    rf, cf, val, agg = spec["rows"], spec.get("columns"), spec.get("values"), spec["agg"]
    if len(df) == 0:
        return _finish(spec, cfg, [], [], {}, {}, {}, 0.0 if agg in ADDITIVE_AGGS or agg == "count_unique" else None, 0)
    df = prepare_values(df, spec)
    keys = [rf] + ([cf] if cf else [])
    cell_s = _pd_agg(df.groupby(keys, sort=False, dropna=False), agg, val)
    cell = {}
    for idx, v in cell_s.items():
        r, c = idx if cf else (idx, None)
        cell[(r, c)] = v
    rtot = dict(_pd_agg(df.groupby(rf, sort=False, dropna=False), agg, val).items())
    ctot = dict(_pd_agg(df.groupby(cf, sort=False, dropna=False), agg, val).items()) if cf else {}
    if agg == "count_rows":
        grand = float(len(df))
    else:
        grand = {"sum": df[val].sum, "mean": df[val].mean, "count": df[val].count, "min": df[val].min,
                 "max": df[val].max, "median": df[val].median, "count_unique": df[val].nunique}[agg]()
    covered = int(sum(_pd_agg(df.groupby(rf, sort=False, dropna=False), "count_rows", None).values))
    rows_present = list(rtot.keys())
    cols_present = list(ctot.keys())
    return _finish(spec, cfg, rows_present, cols_present, cell, rtot, ctot, grand, covered)


def _py_agg(values: list, agg: str, n_records: int):
    nums = [v for v in values if is_present(v)]
    if agg == "count_rows":
        return float(n_records)
    if agg == "count_unique":
        return float(len(set(nums)))
    if agg == "count":
        return float(len(nums))
    if agg == "sum":
        return math.fsum(nums)
    if not nums:
        return None
    if agg == "mean":
        return math.fsum(nums) / len(nums)
    if agg == "min":
        return min(nums)
    if agg == "max":
        return max(nums)
    return statistics.median(nums)


def independent_pivot(df: pd.DataFrame, spec: dict, cfg: dict) -> PivotResult:
    """Second implementation using only Python built-ins (no pandas groupby)."""
    rf, cf, val, agg = spec["rows"], spec.get("columns"), spec.get("values"), spec["agg"]
    rcol = df[rf].tolist()
    ccol = df[cf].tolist() if cf else [None] * len(rcol)
    vcol = df[val].tolist() if val else [None] * len(rcol)
    cells, rows, cols, all_vals = {}, {}, {}, []
    for r, c, v in zip(rcol, ccol, vcol):
        for store, key in ((cells, (r, c)), (rows, r), (cols, c)):
            store.setdefault(key, []).append(v)
        all_vals.append(v)
    cell = {k: _py_agg(v, agg, len(v)) for k, v in cells.items()}
    rtot = {k: _py_agg(v, agg, len(v)) for k, v in rows.items()}
    ctot = {k: _py_agg(v, agg, len(v)) for k, v in cols.items()} if cf else {}
    grand = _py_agg(all_vals, agg, len(all_vals))
    covered = sum(len(v) for v in rows.values())
    return _finish(spec, cfg, list(rows), list(cols) if cf else [], cell, rtot, ctot, grand, covered)


def to_frame(pr: PivotResult) -> pd.DataFrame:
    if pr.has_cols:
        data = {pr.spec["rows"]: pr.row_labels + ["Total"]}
        for j, c in enumerate(pr.col_labels):
            data[c] = [row[j] for row in pr.matrix] + [pr.col_totals[j]]
        data["Total"] = pr.row_totals + [pr.grand_total]
    else:
        data = {pr.spec["rows"]: pr.row_labels + ["Total"],
                pr.label: [row[0] for row in pr.matrix] + [pr.grand_total]}
    return pd.DataFrame(data)


def percent_matrix(pr: PivotResult):
    """Percent-of table from a verified additive pivot. Returns (matrix, row_tot_pct, col_tot_pct, grand_pct) as fractions."""
    mode = pr.spec.get("percent_of")
    if not mode:
        return None

    def frac(a, b):
        return None if (a is None or b in (None, 0)) else a / b

    mat = []
    for i, row in enumerate(pr.matrix):
        out = []
        for j, v in enumerate(row):
            if mode == "row":
                den = pr.row_totals[i]
            elif mode == "column":
                den = pr.col_totals[j]
            else:
                den = pr.grand_total
            out.append(frac(v, den))
        mat.append(out)
    if mode == "row":
        row_t = [1.0 if (pr.row_totals[i] not in (None, 0)) else None for i in range(len(pr.row_labels))]
        col_t = [frac(c, pr.grand_total) for c in pr.col_totals]
        grand = 1.0 if pr.grand_total not in (None, 0) else None
    elif mode == "column":
        row_t = [frac(r, pr.grand_total) for r in pr.row_totals]
        col_t = [1.0 if c not in (None, 0) else None for c in pr.col_totals]
        grand = 1.0 if pr.grand_total not in (None, 0) else None
    else:
        row_t = [frac(r, pr.grand_total) for r in pr.row_totals]
        col_t = [frac(c, pr.grand_total) for c in pr.col_totals]
        grand = 1.0 if pr.grand_total not in (None, 0) else None
    return mat, row_t, col_t, grand
