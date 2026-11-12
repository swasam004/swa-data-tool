"""Explicit, auditable cleaning.

Rules that protect accuracy:
  * A row is never silently altered into something different or dropped.
    Any row with a problem goes to the quarantine list WITH the reason.
  * Row accounting must balance:  rows read = clean + quarantined + blank.
  * Dates are parsed only with formats you configured (no day/month guessing).
  * Numbers are parsed strictly (bad thousands grouping, 'nan', 'inf' are rejected).
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .readers import SOURCE_ROW, RawTable
from .util import MISSING_LABEL

_BODY_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
ISO_FALLBACKS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                 "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"]


@dataclass
class CleanResult:
    clean: pd.DataFrame
    quarantined: pd.DataFrame
    n_read: int
    n_blank: int
    n_clean: int
    n_quarantined: int
    quarantine_breakdown: list = field(default_factory=list)   # [(column, problem, count)]
    warnings: list = field(default_factory=list)
    outliers: list = field(default_factory=list)
    outlier_total: int = 0
    near_duplicates: list = field(default_factory=list)
    stats: list = field(default_factory=list)
    completeness: list = field(default_factory=list)
    multiselect: list = field(default_factory=list)


# ---------------------------------------------------------------- number parsing
def parse_number(text: str, dec: str, thou: str, symbols: list[str]):
    """Return (value, None) or (None, reason)."""
    t = text.strip()
    neg = 0
    if len(t) >= 2 and t[0] == "(" and t[-1] == ")":
        neg += 1
        t = t[1:-1].strip()
    changed = True
    while changed and t:
        changed = False
        if t[0] in "+-":
            neg += 1 if t[0] == "-" else 0
            t = t[1:].strip()
            changed = True
            continue
        if t[-1] == "-":
            neg += 1
            t = t[:-1].strip()
            changed = True
            continue
        low = t.casefold()
        for s in symbols:
            sl = s.casefold()
            if low.startswith(sl):
                t = t[len(s):].strip(); changed = True; break
            if low.endswith(sl):
                t = t[:-len(s)].strip(); changed = True; break
    if neg > 1:
        return None, "ambiguous sign"
    if not t:
        return None, "not a valid number"
    if dec in t:
        if t.count(dec) > 1:
            return None, "not a valid number"
        int_part, frac = t.split(dec, 1)
    else:
        int_part, frac = t, None
    if thou:
        if thou in int_part:
            pat = r"^\d{1,3}(" + re.escape(thou) + r"\d{3})+$"
            if not re.match(pat, int_part):
                return None, "thousands separator in the wrong place"
            int_part = int_part.replace(thou, "")
    body = int_part if frac is None else f"{int_part}.{frac}"
    if not _BODY_RE.match(body):
        return None, "not a valid number"
    try:
        v = float(body)
    except ValueError:
        return None, "not a valid number"
    if not math.isfinite(v):
        return None, "not a valid number"
    v = -v if neg else v
    return v + 0.0, None


# ------------------------------------------------------------------ date parsing
def parse_date(text: str, formats: list[str], cache: dict):
    if text in cache:
        return cache[text]
    result = None
    for fmt in list(formats) + ISO_FALLBACKS:
        try:
            result = _dt.datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue
    cache[text] = result
    return result


def _norm_cell(v, collapse: bool, missing: set) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\u00a0", " ").strip()
    if collapse:
        s = " ".join(s.split())
    if s.casefold() in missing:
        return ""
    return s


def _apply_case(s: str, case: str) -> str:
    return {"keep": s, "lower": s.lower(), "upper": s.upper(), "title": s.title()}[case]


def clean_table(raw: pd.DataFrame, table: RawTable, cfg: dict, extra_warnings: list) -> CleanResult:
    cols = cfg["columns"]
    names = [c["name"] for c in cols]
    missing_tokens = set(cfg["missing_tokens"])
    n = len(raw)
    src = raw[SOURCE_ROW].tolist()
    warnings = list(extra_warnings)

    # 1. normalise text, apply missing tokens
    norm: dict[str, list[str]] = {}
    for c in cols:
        collapse = c["type"] in ("text", "category")
        norm[c["name"]] = [_norm_cell(v, collapse, missing_tokens) for v in raw[c["name"]].tolist()]

    # 2. blank rows (every configured column empty)
    is_blank = [all(norm[nm][i] == "" for nm in names) for i in range(n)]
    blank_in_df = sum(is_blank)

    reasons: dict[int, list[tuple[str, str, str]]] = defaultdict(list)   # idx -> (col, problem, detail)
    typed: dict[str, list] = {}

    # 3. column by column
    for c in cols:
        nm, ctype = c["name"], c["type"]
        vals = norm[nm]
        out: list = [None] * n
        date_cache: dict = {}
        for i in range(n):
            if is_blank[i]:
                continue
            v = vals[i]
            if v == "":
                if c["required"]:
                    reasons[i].append((nm, "required value is missing", ""))
                continue
            det = (lambda x: "") if c["pii"] else (lambda x: x)      # never echo personal data
            if ctype == "multiselect":
                sep = c["separator"]
                parts = v.split() if sep == " " else [x.strip() for x in v.split(sep)]
                canon = {o.casefold(): o for o in c["options"]}
                chosen, unknown = set(), []
                for part in parts:
                    if not part:
                        continue
                    if part.casefold() in canon:
                        chosen.add(canon[part.casefold()])
                    else:
                        unknown.append(part)
                if unknown:
                    reasons[i].append((nm, "unknown option(s) not in the configured list", repr(unknown)))
                    continue
                if not chosen:
                    continue
                out[i] = [o for o in c["options"] if o in chosen]
            elif ctype in ("text", "category"):
                v = _apply_case(v, c["case"])
                if c["mapping"]:
                    v = c["mapping"].get(" ".join(v.split()).casefold(), v)
                if c["allowed"] is not None:
                    canon = {a.casefold(): a for a in c["allowed"]}
                    if v.casefold() in canon:
                        v = canon[v.casefold()]
                    else:
                        reasons[i].append((nm, "value not in the allowed list", det(repr(v))))
                        continue
                out[i] = v
            elif ctype == "number":
                num, why = parse_number(v, c["decimal_separator"], c["thousands_separator"], cfg["currency_symbols"])
                if why:
                    reasons[i].append((nm, why, repr(v)))
                    continue
                if c["integer"] and num != math.floor(num):
                    reasons[i].append((nm, "must be a whole number", repr(v)))
                    continue
                if c["min"] is not None and num < c["min"]:
                    reasons[i].append((nm, f"below minimum {c['min']:g}", repr(v)))
                    continue
                if c["max"] is not None and num > c["max"]:
                    reasons[i].append((nm, f"above maximum {c['max']:g}", repr(v)))
                    continue
                if c["bands"] and num < c["bands"]["edges"][0]:
                    reasons[i].append((nm, f"below the first band edge {c['bands']['edges'][0]:g}", repr(v)))
                    continue
                out[i] = num
            else:  # date
                d = parse_date(v, c["date_formats"], date_cache)
                if d is None:
                    reasons[i].append((nm, "not a valid date for the configured format(s)", repr(v)))
                    continue
                if c["min_date"] and d < c["min_date"]:
                    reasons[i].append((nm, f"date before {c['min_date']}", repr(v)))
                    continue
                if c["max_date"] and d > c["max_date"]:
                    reasons[i].append((nm, f"date after {c['max_date']}", repr(v)))
                    continue
                out[i] = d
        typed[nm] = out

    # 4. unique columns: first valid occurrence wins, later ones are quarantined
    for c in cols:
        if not c["unique"]:
            continue
        seen: dict = {}
        for i in range(n):
            if is_blank[i] or typed[c["name"]][i] is None or i in reasons:
                continue
            key = typed[c["name"]][i]
            if key in seen:
                reasons[i].append((c["name"], f"duplicate of an earlier row (first seen at source row {src[seen[key]]})", "" if c["pii"] else repr(key)))
            else:
                seen[key] = i

    keep_idx = [i for i in range(n) if not is_blank[i] and i not in reasons]
    quar_idx = [i for i in range(n) if not is_blank[i] and i in reasons]

    # 5. clean frame
    data = {SOURCE_ROW: [src[i] for i in keep_idx]}
    for c in cols:
        series = [typed[c["name"]][i] for i in keep_idx]
        if c["type"] == "number":
            data[c["name"]] = np.array([np.nan if v is None else v for v in series], dtype="float64")
        elif c["type"] == "date":
            data[c["name"]] = series            # python date or None
        elif c["type"] == "category":
            data[c["name"]] = [MISSING_LABEL if v is None else v for v in series]
        elif c["type"] == "multiselect":
            data[c["name"]] = ["" if v is None else "; ".join(v) for v in series]
        else:
            data[c["name"]] = ["" if v is None else v for v in series]
    for d in cfg["derived"]:
        srcname = d["source"]
        base = [typed[srcname][i] for i in keep_idx]
        if d["kind"] in ("year", "quarter", "month"):
            fmtters = {"year": lambda x: f"{x.year:04d}",
                       "quarter": lambda x: f"{x.year:04d}-Q{(x.month - 1) // 3 + 1}",
                       "month": lambda x: f"{x.year:04d}-{x.month:02d}"}
            data[d["name"]] = [MISSING_LABEL if x is None else fmtters[d["kind"]](x) for x in base]
        elif d["kind"] == "band":
            edges, labels = d["edges"], d["labels"]
            data[d["name"]] = [MISSING_LABEL if x is None else labels[bisect.bisect_right(edges, x) - 1] for x in base]
        else:  # option of a multiselect: 1 selected, 0 not selected, blank when the question was unanswered
            data[d["name"]] = np.array([np.nan if x is None else (1.0 if d["option"] in x else 0.0) for x in base], dtype="float64")
    clean = pd.DataFrame(data)
    for c in cols:
        if c["type"] == "date":
            clean[c["name"]] = pd.to_datetime(pd.Series(data[c["name"]], dtype=object), errors="raise")

    # 6. quarantine frame (original text, so nothing is lost)
    q_rows = []
    for i in quar_idx:
        rec = {SOURCE_ROW: src[i],
               "Reasons": "; ".join(f"{col}: {prob}" + (f" ({det})" if det else "") for col, prob, det in reasons[i])}
        for nm in names:
            rec[nm] = raw[nm].iloc[i]
        q_rows.append(rec)
    for src_row, fields, why in table.malformed:
        rec = {SOURCE_ROW: src_row, "Reasons": f"Malformed row: {why}"}
        for nm in names:
            rec[nm] = ""
        rec[names[0]] = ("(row content hidden: this dataset contains protected personal data)"
                         if any(c["pii"] for c in cols) else " | ".join(fields)[:500])
        q_rows.append(rec)
    quarantined = pd.DataFrame(q_rows, columns=[SOURCE_ROW, "Reasons"] + names)
    if len(quarantined):
        quarantined = quarantined.sort_values(SOURCE_ROW, kind="stable").reset_index(drop=True)

    counter: Counter = Counter()
    for i in quar_idx:
        for col, prob, _ in reasons[i]:
            key = (col, re.sub(r"\(first seen at source row \d+\)", "", prob).strip())
            counter[key] += 1
    if table.malformed:
        counter[("(whole row)", "malformed row (wrong number of fields)")] += len(table.malformed)
    breakdown = [(c_, p_, k) for (c_, p_), k in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]

    n_blank_total = blank_in_df + table.n_blank_lines
    n_read = n + len(table.malformed) + table.n_blank_lines
    res = CleanResult(
        clean=clean, quarantined=quarantined, n_read=n_read, n_blank=n_blank_total,
        n_clean=len(keep_idx), n_quarantined=len(quar_idx) + len(table.malformed),
        quarantine_breakdown=breakdown,
    )

    # 7. warnings that do not remove data
    if res.n_quarantined:
        warnings.append(f"{res.n_quarantined} row(s) were quarantined and are NOT in the analysis - see 'Quarantined Rows'.")
    if res.n_blank:
        warnings.append(f"{res.n_blank} blank row(s) were skipped.")
    for c in cols:
        if c["type"] != "category":
            continue
        groups: dict[str, Counter] = defaultdict(Counter)
        for v in clean[c["name"]]:
            if v != MISSING_LABEL:
                groups[v.casefold()][v] += 1
        for _, spellings in groups.items():
            if len(spellings) > 1:
                res.near_duplicates.append({"column": c["name"], "spellings": dict(spellings)})
        n_near = sum(1 for x in res.near_duplicates if x["column"] == c["name"])
        if n_near:
            warnings.append(
                f"Column '{c['name']}' has {n_near} value(s) spelled in more than one way (case differences). "
                "They are counted separately in pivots. Fix with `case:` or `mapping:` in the configuration."
            )
        miss = int((clean[c["name"]] == MISSING_LABEL).sum())
        if miss:
            warnings.append(f"Column '{c['name']}' has {miss} blank value(s), shown as '{MISSING_LABEL}' in pivots so totals still reconcile.")
    ident = int(clean.duplicated(subset=names, keep="first").sum()) if len(clean) else 0
    if ident:
        warnings.append(f"{ident} row(s) are identical to an earlier row in every column. They were KEPT. "
                        "If that is wrong, mark an ID column `unique: true` to reject repeats.")
    for c in cols:
        if c["type"] == "date":
            miss = int(clean[c["name"]].isna().sum())
            if miss:
                warnings.append(f"Date column '{c['name']}' has {miss} blank value(s) (grouped as '{MISSING_LABEL}').")

    # 8. outliers (flagged only, never removed) and descriptive statistics
    for c in cols:
        if c["type"] != "number":
            continue
        s = clean[c["name"]].dropna()
        stat = {"column": c["name"], "count": int(s.count()), "missing": int(clean[c["name"]].isna().sum()),
                "sum": math.fsum(s.tolist()), "decimals": c["decimals"]}
        if len(s):
            stat.update(mean=float(s.mean()), median=float(s.median()),
                        std=float(s.std(ddof=1)) if len(s) > 1 else None,
                        min=float(s.min()), max=float(s.max()))
        res.stats.append(stat)
        if len(s) >= 8:
            q1, q3 = np.percentile(s, [25, 75])
            iqr = q3 - q1
            if iqr > 0:
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                flagged = clean.loc[(clean[c["name"]] < lo) | (clean[c["name"]] > hi), [SOURCE_ROW, c["name"]]]
                if len(flagged):
                    res.outlier_total += len(flagged)
                    warnings.append(f"Column '{c['name']}': {len(flagged)} unusual value(s) outside [{lo:,.2f}, {hi:,.2f}] (flagged, kept).")
                    for _, r in flagged.head(100).iterrows():
                        res.outliers.append({"column": c["name"], "source_row": int(r[SOURCE_ROW]),
                                             "value": float(r[c["name"]]), "low": float(lo), "high": float(hi)})
    # 9. completeness of every configured column among the analysed rows
    for c in cols:
        col = clean[c["name"]]
        if c["type"] == "category":
            miss = int((col == MISSING_LABEL).sum())
        elif c["type"] in ("text", "multiselect"):
            miss = int((col == "").sum())
        else:
            miss = int(col.isna().sum())
        res.completeness.append({"column": c["name"], "required": c["required"], "missing": miss,
                                 "pct_complete": (1 - miss / len(clean)) if len(clean) else None})
    # 10. multi-select questions: respondents choosing each option
    for c in cols:
        if c["type"] != "multiselect":
            continue
        opts = []
        answered = int((clean[c["name"]] != "").sum())
        for o in c["options"]:
            n_sel = int(clean[f"{c['name']}: {o}"].sum())
            opts.append({"option": o, "selected": n_sel, "pct": (n_sel / answered) if answered else None})
        res.multiselect.append({"column": c["name"], "answered": answered, "options": opts})
    res.warnings = warnings
    return res
