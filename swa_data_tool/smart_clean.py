"""Smart cleaning: turn the found table into a typed dataset, logging every change in plain words."""
from __future__ import annotations

import datetime as _dt
import math
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .cleaning import parse_number
from .smart_load import Block, Scan, SmartError, blank

MISSING = {"", "na", "n/a", "nan", "null", "none", "-", "--", "?", "#n/a", "#value!", "#div/0!", "#ref!", "#name?", "#null!", "#num!", "nil", "unknown"}
CURRENCY = ["KES", "KSh", "Ksh", "Kshs", "USD", "UGX", "TZS", "EUR", "GBP", "$", "£", "€"]
YEAR_NAME = re.compile(r"(^|[^a-z])(year|yr|yyyy)([^a-z]|$)", re.I)
ID_NAME = re.compile(r"(^|[^a-z])(id|code|ref|reference|serial|invoice|order|ticket|no\.?|number|#)([^a-z]|$)", re.I)
PII_STRONG = re.compile(r"(full\s*name|first\s*name|last\s*name|sur\s*name|surname|given\s*name|middle\s*name|(beneficiary|participant|respondent|client|patient|customer|student|employee)\s*name|phone|mobile|tel(ephone)?\b|e-?mail|address|national\s*id|id\s*number|passport|\bnin\b|\bssn\b)", re.I)
PII_PHONE_ID = re.compile(r"(phone|mobile|tel(ephone)?\b|national\s*id|id\s*number|passport|\bnin\b|\bssn\b)", re.I)
BOOL_SETS = [{"yes", "no"}, {"y", "n"}, {"true", "false"}, {"t", "f"}, {"male", "female"}]
FIRSTWORD_BASE = re.compile(r"(car|vehicle|auto|model|product|item|brand|make)", re.I)
BRAND_ALIASES = ["brand", "make", "maker", "manufacturer", "company", "marque"]
DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]
SLASH_DATE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$")


@dataclass
class Col:
    name: str
    role: str                                  # id | category | boolean | measure | year | date | text
    source: str = ""                           # header as found in the file
    decimals: int = 0
    integer: bool = False
    prefix: str = ""                           # unit shown before numbers, e.g. "$"
    pii: str | None = None
    aliases: list = field(default_factory=list)
    derived_from: str | None = None
    note: str = ""


@dataclass
class Dataset:
    df: pd.DataFrame
    cols: list
    row_numbers: list
    log: list = field(default_factory=list)            # [{"kind","column","message"}]
    source: dict = field(default_factory=dict)         # sheet, ref, rows, cols
    notes: list = field(default_factory=list)
    lookups: list = field(default_factory=list)        # [{"sheet","key","value","column","matched","unmatched"}]

    def col(self, name):
        return next((c for c in self.cols if c.name == name), None)

    def by_role(self, *roles):
        return [c for c in self.cols if c.role in roles]


def _ws(v) -> str:
    return re.sub(r"\s+", " ", str(v).replace("\u00a0", " ")).strip()


def _log(ds_log, kind, column, message):
    ds_log.append({"kind": kind, "column": column, "message": message})


def _examples(items, n=3):
    out = []
    for x in items:
        if x not in out:
            out.append(x)
        if len(out) == n:
            break
    return ", ".join(repr(x) for x in out)


# --------------------------------------------------------------------------------------- inference
def _date_from_strings(vals):
    """Return (list of datetime|None, note) if EVERY value is an unambiguous date, else (None, note)."""
    for fmt in DATE_FORMATS:
        try:
            return [_dt.datetime.strptime(v, fmt) for v in vals], ""
        except ValueError:
            continue
    m = [SLASH_DATE.match(v) for v in vals]
    if all(m):
        a = [int(x.group(1)) for x in m]
        b = [int(x.group(2)) for x in m]
        if max(a) > 12 and max(b) <= 12:
            order = "dmy"
        elif max(b) > 12 and max(a) <= 12:
            order = "mdy"
        else:
            return None, "These look like dates but day/month order is ambiguous (for example 03/04/2025), so I kept them as text. Tell me the order if you want them treated as dates."
        out = []
        for x in m:
            d, mo, y = (int(x.group(1)), int(x.group(2)), int(x.group(3))) if order == "dmy" else (int(x.group(2)), int(x.group(1)), int(x.group(3)))
            y = y + 2000 if y < 100 else y
            try:
                out.append(_dt.datetime(y, mo, d))
            except ValueError:
                return None, ""
        return out, f"Read as {'day/month/year' if order == 'dmy' else 'month/day/year'} because the numbers only make sense that way."
    return None, ""


def _clean_column(name: str, raw: list, log: list) -> tuple[pd.Series, Col]:
    """Infer the type of one column and return a cleaned Series + its description."""
    n = len(raw)
    vals, ws_fixed = [], []
    for v in raw:
        if isinstance(v, str):
            c = _ws(v)
            if c != v:
                ws_fixed.append(v)
            v = c
            if v.casefold() in MISSING:
                v = None
        elif isinstance(v, float) and math.isnan(v):
            v = None
        vals.append(v)
    present = [v for v in vals if v is not None]
    n_missing = n - len(present)
    col = Col(name=name, role="text", source=name)
    if not present:
        _log(log, "type", name, "The column is empty.")
        return pd.Series([None] * n, dtype=object), col
    # error values like #DIV/0! were already mapped to missing
    # ---- dates (real Excel dates)
    if all(isinstance(v, (_dt.datetime, _dt.date)) for v in present):
        col.role = "date"
        ser = pd.Series([pd.Timestamp(v) if v is not None else pd.NaT for v in vals])
        return ser, col
    # ---- numbers
    numeric_native = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present)
    parsed, bad, prefix, pct = [], [], "", False
    if numeric_native:
        parsed = [float(v) if v is not None else None for v in vals]
    else:
        strs = [v for v in present if isinstance(v, str)]
        lead_zero = any(re.match(r"^0\d", s) for s in strs)
        for v in vals:
            if v is None:
                parsed.append(None)
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                parsed.append(float(v))
                continue
            s = v if isinstance(v, str) else str(v)
            t = s[:-1].strip() if s.endswith("%") else s
            num, why = parse_number(t, ".", ",", CURRENCY)
            if why is None and not lead_zero:
                parsed.append(num)
                if s.endswith("%"):
                    pct = True
                m = re.match(r"^\s*[(+-]*\s*(KES|KSh|Ksh|Kshs|USD|UGX|TZS|EUR|GBP|\$|£|€)", s)
                if m and not prefix:
                    prefix = m.group(1)
            else:
                parsed.append(None)
                bad.append(s)
        numeric_native = False
    ok_numbers = [p for p in parsed if p is not None]
    if numeric_native or (len(ok_numbers) >= 2 and len(bad) <= max(0, int(0.1 * len(present)))):
        ints = all(float(p).is_integer() for p in ok_numbers)
        col.decimals = 0 if ints else min(max((len(repr(p).split(".")[1]) if "." in repr(p) and "e" not in repr(p) else 0) for p in ok_numbers), 4)
        col.integer = ints
        col.prefix = "" if pct else prefix
        if pct:
            col.prefix = ""
            col.note = "percent"
        if not numeric_native:
            if prefix:
                _log(log, "currency", name, f"Removed the '{prefix}' sign from {len(present) - len(bad)} value(s) and stored them as numbers.")
            else:
                _log(log, "type", name, f"Stored {len(ok_numbers)} text value(s) as numbers.")
            if bad:
                _log(log, "blank", name, f"{len(bad)} value(s) could not be read as numbers ({_examples(bad)}) and were left blank; their rows are kept.")
        if YEAR_NAME.search(name) and ints and all(1900 <= p <= 2100 for p in ok_numbers):
            col.role = "year"
        elif ID_NAME.search(name) and len(set(ok_numbers)) >= 0.95 * len(ok_numbers) and len(ok_numbers) >= 5:
            col.role = "id"
        else:
            col.role = "measure"
        return pd.Series(parsed, dtype="float64"), col
    # ---- text dates
    strs = [v for v in present if isinstance(v, str)]
    if len(strs) == len(present):
        dates, note = _date_from_strings(strs)
        if dates is not None:
            it = iter(dates)
            col.role = "date"
            _log(log, "type", name, f"Read the text as dates. {note}".strip())
            return pd.Series([pd.Timestamp(next(it)) if v is not None else pd.NaT for v in vals]), col
        if note:
            _log(log, "type", name, note)
    # ---- text-like
    texts = [str(v) if v is not None else None for v in vals]
    if ws_fixed:
        fixed = [t for t in dict.fromkeys(ws_fixed)]
        _log(log, "trim", name, f"Removed extra spaces from {len(ws_fixed)} value(s) (for example {_examples(fixed)}).")
    distinct = len({t for t in texts if t is not None})
    lowers = {t.casefold() for t in texts if t is not None}
    n_present = len(present)
    low_set = {x.casefold() for x in {t for t in texts if t is not None}}
    if low_set in [s for s in BOOL_SETS] or (low_set <= {"yes", "no", "y", "n", "true", "false"} and len(low_set) == 2):
        col.role = "boolean"
    elif n_present >= 5 and distinct >= 0.95 * n_present:
        avg = sum(len(t) for t in texts if t) / n_present
        col.role = "id" if avg <= 30 else "text"
    elif distinct <= 50 or distinct <= 0.5 * n_present:
        col.role = "category"
    else:
        col.role = "text"
    # unify spelling variants that differ only by upper/lower case
    if col.role in ("category", "boolean"):
        groups = OrderedDict()
        for t in texts:
            if t is not None:
                groups.setdefault(t.casefold(), Counter())[t] += 1
        mapping = {}
        for k, cnt in groups.items():
            if len(cnt) > 1:
                best = max(cnt.items(), key=lambda kv: (kv[1], -list(cnt).index(kv[0])))[0]
                for spelling in cnt:
                    if spelling != best:
                        mapping[spelling] = best
        if mapping:
            texts = [mapping.get(t, t) if t is not None else None for t in texts]
            _log(log, "case", name, "Merged spelling variants that differ only in capital letters: " + ", ".join(f"{a!r} -> {b!r}" for a, b in list(mapping.items())[:4]) + ".")
    return pd.Series(texts, dtype=object), col


# --------------------------------------------------------------------------------------- derived & joins
def _norm_key(v) -> str:
    return _ws(v).casefold() if v is not None and not (isinstance(v, float) and math.isnan(v)) else ""


def _first_word_series(ser: pd.Series) -> pd.Series:
    return ser.map(lambda v: _ws(v).split(" ")[0] if isinstance(v, str) and _ws(v) else None)


def _alias_words(text: str) -> list:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def build_dataset(scan: Scan) -> Dataset:
    log: list = []
    primary = scan.primary
    first = primary[0]
    header = first.header
    rows, row_numbers = [], []
    for b in primary:
        for r, rn in zip(b.rows, b.row_numbers):
            rows.append(list(r)); row_numbers.append(rn)
    stacked = len(primary) > 1
    if stacked:
        header = list(header) + ["Source Sheet"]
        k = 0
        out = []
        for b in primary:
            for r in b.rows:
                out.append(list(r) + [b.sheet]); k += 1
        rows = out
    cols_raw = list(zip(*rows)) if rows else []
    data, cols = OrderedDict(), []
    seen_names = set()
    for i, h in enumerate(header):
        name = h
        if name.casefold() in seen_names or name == "Source Row":
            name = f"{h} (2)"
        seen_names.add(name.casefold())
        ser, col = _clean_column(name, list(cols_raw[i]), log)
        col.source = h
        data[name] = ser
        cols.append(col)
    df = pd.DataFrame(data)
    ds = Dataset(df=df, cols=cols, row_numbers=row_numbers, log=log,
                 source={"sheet": first.sheet, "ref": first.ref, "rows": len(df), "cols": len(cols), "kind": scan.file_kind,
                         "stacked": [f"{b.sheet}!{b.ref}" for b in primary]})
    ds.notes = list(scan.notes)
    if first.synthetic_header:
        _log(log, "type", "", "The table has no header row, so columns were named Column A, Column B ...")
    # --- personal data protection (by header name)
    sexlike = any(re.search(r"\b(sex|gender|age|dob|ward|village|county|household|beneficiar)", c.name, re.I) for c in cols)
    for c in cols:
        strong = bool(PII_STRONG.search(c.name))
        plain_name = bool(re.fullmatch(r"\s*(name|names)\s*", c.name, re.I)) and sexlike
        if (strong or plain_name) and c.role in ("id", "text", "category", "measure"):
            if c.role == "measure" and not PII_PHONE_ID.search(c.name):
                continue
            c.pii = "mask" if PII_PHONE_ID.search(c.name) else "hash"
            c.role = "text"
            if c.role == "text" and df[c.name].dtype != object:
                df[c.name] = df[c.name].map(lambda v: None if pd.isna(v) else str(int(v)) if float(v).is_integer() else str(v))
            _log(log, "privacy", c.name, f"Looks like personal data, so it is {'masked' if c.pii == 'mask' else 'hashed'} in every output (analysis still counts distinct people correctly).")
    # --- derived first-word + lookup joins
    used_lookup_cols = set()
    for lk in scan.lookups:
        keys = {_norm_key(r[0]): _ws(r[1]) for r in lk.rows}
        best = None
        for c in list(cols):
            if c.role not in ("category", "text", "id") or c.pii:
                continue
            ser = df[c.name]
            nonnull = [v for v in ser if isinstance(v, str)]
            if len(nonnull) < 2:
                continue
            direct = sum(1 for v in nonnull if _norm_key(v) in keys) / len(nonnull)
            fw = [w for w in _first_word_series(ser) if w]
            first_ratio = sum(1 for w in fw if _norm_key(w) in keys) / max(len(fw), 1)
            for mode, ratio in (("direct", direct), ("first", first_ratio)):
                if ratio >= 0.6 and (best is None or ratio > best[0]):
                    best = (ratio, mode, c)
        if best is None:
            ds.notes.append(f"The lookup table '{lk.sheet}' ({lk.header[0]} -> {lk.header[1]}) did not match any column, so it was not used.")
            continue
        ratio, mode, c = best
        key_name, val_name = lk.header[0], lk.header[1]
        if mode == "first":
            kcol = key_name if key_name.casefold() not in {x.name.casefold() for x in cols} else f"{c.name} (first word)"
            df[kcol] = _first_word_series(df[c.name])
            kc = Col(name=kcol, role="category", derived_from=c.name, source=f"first word of {c.name}")
            kc.aliases = sorted(set(BRAND_ALIASES + _alias_words(key_name))) if FIRSTWORD_BASE.search(c.name) or key_name.casefold() in BRAND_ALIASES else _alias_words(key_name)
            cols.insert(cols.index(c) + 1, kc)
            _log(log, "derive", kcol, f"Created '{kcol}' from the first word of '{c.name}' (for example '{df[c.name].dropna().iloc[0]}' -> '{df[kcol].dropna().iloc[0]}').")
            key_series = df[kcol]
        else:
            kcol, key_series = c.name, df[c.name]
        vname = val_name if val_name.casefold() not in {x.name.casefold() for x in cols} else f"{val_name} ({lk.sheet})"
        mapped = key_series.map(lambda v: keys.get(_norm_key(v)) if isinstance(v, str) else None)
        unmatched = sorted({v for v, m in zip(key_series, mapped) if isinstance(v, str) and pd.isna(m)})
        df[vname] = mapped
        vc = Col(name=vname, role="category", derived_from=kcol, source=f"{lk.sheet}!{lk.ref}",
                 aliases=sorted(set(_alias_words(lk.sheet) + _alias_words(val_name) + _alias_words(lk.sheet + " " + val_name))))
        cols.insert(cols.index(next(x for x in cols if x.name == kcol)) + 1, vc)
        matched = int(mapped.notna().sum())
        _log(log, "join", vname, f"Looked up '{vname}' for each '{kcol}' using sheet '{lk.sheet}' ({matched} of {int(key_series.notna().sum())} matched).")
        if unmatched:
            _log(log, "blank", vname, f"No match in '{lk.sheet}' for: {', '.join(unmatched[:8])}. Those rows have a blank {vname}.")
        ds.lookups.append({"sheet": lk.sheet, "ref": lk.ref, "key": kcol, "value": vname, "matched": matched, "unmatched": unmatched})
    # --- generic first-word split when there is no lookup but the pattern is obvious
    if not ds.lookups:
        for c in list(cols):
            if c.role != "category" or c.pii or not FIRSTWORD_BASE.search(c.name):
                continue
            ser = df[c.name]
            nonnull = [v for v in ser if isinstance(v, str)]
            if len(nonnull) >= 6 and sum(1 for v in nonnull if " " in v) >= 0.9 * len(nonnull):
                fw = [w for w in _first_word_series(ser) if w]
                if 2 <= len(set(fw)) <= 0.6 * len({*nonnull}) and sum(1 for w in fw if re.fullmatch(r"[A-Za-z][A-Za-z\-]+", w)) >= 0.9 * len(fw):
                    nm = f"{c.name} (first word)"
                    df[nm] = _first_word_series(ser)
                    cols.insert(cols.index(c) + 1, Col(name=nm, role="category", derived_from=c.name, aliases=BRAND_ALIASES, source=f"first word of {c.name}"))
                    _log(log, "derive", nm, f"Created '{nm}' from the first word of '{c.name}'.")
    # --- overall hygiene notes
    dups = int(df[[c.name for c in cols if c.derived_from is None]].duplicated().sum())
    if dups:
        _log(log, "duplicate", "", f"{dups} row(s) are exact copies of an earlier row. They were kept and counted; check whether they are real repeats.")
    for msg in first.dropped:
        _log(log, "drop", "", msg)
    ds.df = df[[c.name for c in cols]]
    ds.cols = cols
    return ds
