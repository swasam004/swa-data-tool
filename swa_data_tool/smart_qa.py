"""Ask questions about your data in plain English.

How it works (and why you can trust the numbers):
  1. Your question is turned into a small structured QUERY (what to count/average, by which column, with which filters).
     This is done by built-in language rules (no internet, no cost). If the rules cannot understand and an AI key is
     configured, an AI model may translate the question - it only sees column names and summary statistics, never rows.
  2. The query is run by ordinary code - the AI never calculates anything.
  3. Every answer is calculated twice (pandas and plain Python). If the two disagree you get no answer, not a wrong one.
  4. The answer always shows how the question was interpreted and the full table behind it.
"""
from __future__ import annotations

import difflib
import json
import math
import os
import re
import urllib.request
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .checks import close
from .smart_analyze import fmt_num, plural, py_mean, py_pearson, py_slope

SYNONYMS = {
    "price": ["cost", "value", "amount", "pricing", "charge", "fee", "usd"], "cost": ["price"], "revenue": ["sales", "income", "turnover"],
    "sales": ["revenue"], "salary": ["pay", "wage", "wages", "income"], "income": ["salary", "earnings", "revenue"],
    "km": ["kilometer", "kilometre", "mileage", "distance", "odometer", "mile", "driven"], "color": ["colour"], "colour": ["color"],
    "owner": ["ownership"], "transmission": ["gearbox"], "sex": ["gender"], "gender": ["sex"], "country": ["nation", "origin"],
    "region": ["area", "location", "zone", "district", "county"], "location": ["region", "place"], "quantity": ["qty", "units", "volume"],
    "qty": ["quantity"], "age": ["old"], "year": ["yr"],
}
ENTITY_WORDS = {"car", "vehicle", "customer", "client", "product", "item", "patient", "beneficiary", "student", "employee", "order",
                "person", "household", "participant", "respondent", "transaction", "sale", "invoice", "case", "ticket", "student", "pupil"}
STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "is", "are", "was", "were", "be", "what", "which", "who", "how", "do", "does", "did",
        "and", "or", "with", "than", "that", "this", "these", "those", "me", "my", "our", "us", "all", "any", "there", "it", "its", "at", "as", "by"}
OPS = {"gt", "gte", "lt", "lte", "eq"}
INTENTS = {"mode", "antimode", "agg_by", "agg", "count", "share", "corr", "corr_all", "trend", "distinct", "describe", "rows_extreme", "compare", "list"}
AGGS = {"mean", "median", "sum", "min", "max"}


# ====================================================================================== context
@dataclass
class QACol:
    name: str
    role: str
    prefix: str = ""
    decimals: int = 0
    pii: str | None = None
    aliases: list = field(default_factory=list)
    source: str = ""


@dataclass
class QAContext:
    df: pd.DataFrame
    cols: dict                                   # name -> QACol
    noun: str = "rows"
    stats: dict = field(default_factory=dict)

    def visible(self, *roles):
        return [c for c in self.cols.values() if not c.pii and (not roles or c.role in roles)]


def row_noun(cols) -> str:
    for c in cols:
        if c.role in ("category", "id", "text") and not c.pii and c.name.strip().lower() in ENTITY_WORDS:
            return c.name.strip().lower() + "s"
    return "rows"


def context_from_dataset(ds) -> QAContext:
    cols = {}
    for c in ds.cols:
        cols[c.name] = QACol(c.name, c.role, c.prefix, c.decimals, c.pii, list(c.aliases), c.source)
    return QAContext(ds.df.reset_index(drop=True), cols, row_noun(list(cols.values())))


# ====================================================================================== text helpers
def _clean_q(q: str) -> str:
    q = q.lower().replace("what's", "what is").replace("whats", "what is").replace("who's", "who is").replace("it's", "it is")
    q = re.sub(r"\([^)]*\)", " ", q)                      # remarks in brackets, e.g. (refer to the 'Manufacturing country' sheet)
    q = re.sub(r"[\u2018\u2019\u201c\u201d\"']", " ", q)
    return q


def tokens(text: str) -> list:
    return [t for t in re.findall(r"[a-z0-9$%]+(?:\.\d+)?", _clean_q(text))]


def sing(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


# ====================================================================================== aliases
def build_aliases(ctx: QAContext) -> list:
    """[(tokens_tuple, column, strength)] - longer / exact names are stronger."""
    out, vocab = [], {}
    own_single = {}
    for c in ctx.visible():
        toks = [sing(t) for t in tokens(c.name) if t]
        own_single.update({t: c.name for t in toks})
    for c in ctx.cols.values():
        if c.pii:
            continue
        toks = [sing(t) for t in tokens(c.name)]
        if toks:
            out.append((tuple(toks), c.name, 3))
        for t in toks:
            if (len(t) >= 3 or (len(t) == 2 and t not in ("no", "id", "of", "to", "in", "on", "at", "by", "is", "or"))) and t not in ("usd", "the", "per", "and"):
                out.append(((t,), c.name, 2))
                vocab.setdefault(t, c.name)
                for syn in SYNONYMS.get(t, []):
                    if sing(syn) not in own_single:
                        out.append(((sing(syn),), c.name, 1))
        for a in c.aliases:
            at = tuple(sing(t) for t in tokens(a))
            if at:
                out.append((at, c.name, 2))
                vocab.setdefault(at[-1], c.name)
        if "!" in c.source:                                   # a joined column is also known by its sheet name
            sheet = tuple(sing(t) for t in tokens(c.source.split("!")[0]))
            if sheet:
                out.append((sheet, c.name, 3))
                out.append(((sheet[0],), c.name, 1))
    out.sort(key=lambda a: (-len(a[0]), -a[2]))
    return out, vocab


@dataclass
class Mention:
    start: int
    end: int
    kind: str          # col | value | number
    col: str | None = None
    value: object = None


def find_mentions(toks: list, ctx: QAContext, aliases, vocab) -> list:
    stoks = [sing(t) for t in toks]
    taken = [False] * len(toks)
    out = []
    # values of category columns first (longest first) so that 'white' is a value, not noise
    vmap = {}
    for c in ctx.visible("category", "boolean"):
        for v in ctx.df[c.name].dropna().unique():
            vt = tuple(sing(t) for t in tokens(str(v)))
            if vt:
                vmap.setdefault(vt, []).append((c.name, str(v)))
    colspans = []
    for i in range(len(toks)):                          # column names / aliases
        if taken[i]:
            continue
        for at, cname, strength in aliases:
            L = len(at)
            if tuple(stoks[i:i + L]) == at and not any(taken[i:i + L]):
                if L == 1 and at[0] in vmap and strength < 3:        # the word is also a cell value: let the value win
                    continue
                for k in range(i, i + L):
                    taken[k] = True
                out.append(Mention(i, i + L, "col", cname))
                break
    for i in range(len(toks)):
        if taken[i]:
            continue
        for L in range(min(4, len(toks) - i), 0, -1):
            key = tuple(stoks[i:i + L])
            if key in vmap and not any(taken[i:i + L]):
                col, val = vmap[key][0]
                for k in range(i, i + L):
                    taken[k] = True
                out.append(Mention(i, i + L, "value", col, val))
                break
    for i, t in enumerate(toks):                         # fuzzy column words (typos)
        if taken[i] or len(t) < 6 or t in STOP:
            continue
        m = difflib.get_close_matches(sing(t), list(vocab), n=1, cutoff=0.82)
        if m:
            taken[i] = True
            out.append(Mention(i, i + 1, "col", vocab[m[0]]))
    for i, t in enumerate(toks):
        if taken[i]:
            continue
        mm = re.fullmatch(r"(\d+(?:\.\d+)?)(k|m)?", t)
        if mm:
            val = float(mm.group(1)) * (1000 if mm.group(2) == "k" else 1_000_000 if mm.group(2) == "m" else 1)
            out.append(Mention(i, i + 1, "number", value=val))
    return sorted(out, key=lambda m: m.start)


# ====================================================================================== query building
RX = {
    "antimode": re.compile(r"\b(least (popular|common|frequent|sold|used|preferred)|rarest|fewest|lowest (count|frequency|number)|unpopular|uncommon|least often|least number|smallest number)\b"),
    "mode": re.compile(r"\b(most (popular|common|frequent|sold|used|preferred|prevalent|often)|commonest|popular(ity)?|(highest|largest|biggest|greatest) (count|frequency|number)|most number|top selling|best selling)\b"),
    "avg": re.compile(r"\b(average|mean|avg|typical)\b"), "median": re.compile(r"\bmedian\b"),
    "sum": re.compile(r"\b(total|sum|combined|altogether|aggregate|overall)\b"),
    "max": re.compile(r"\b(maximum|max|highest|largest|biggest|greatest|most expensive|priciest|top|best|oldest|longest|newest|latest|most recent)\b"),
    "min": re.compile(r"\b(minimum|min|lowest|smallest|cheapest|least expensive|worst|lightest|shortest|earliest|youngest|bottom)\b"),
    "count": re.compile(r"\b(how many|number of|count of|count|how much)\b"), "percent": re.compile(r"\b(percent|percentage|proportion|share|fraction|what portion|ratio)\b|%"),
    "corr": re.compile(r"\b(correlat\w*|relationship|related|relate\w*|associat\w*|link between|connection|depend\w*|affect\w*|influenc\w*|impact\w*|driver\w*)\b"),
    "trend": re.compile(r"\b(trend\w*|over time|change[sd]?|changing|growth|grow\w*|increas\w*|decreas\w*|evolv\w*|by year|per year|each year|every year|yearly|annual\w*|over the years|year over year|year by year)\b"),
    "distinct": re.compile(r"\b(different|distinct|unique|how many kinds|how many types)\b"), "list": re.compile(r"\b(list|what are the|show all|which .* are there|what .* are there|all the)\b"),
    "describe": re.compile(r"\b(describe|summar\w*|distribution|overview|profile|statistics|stats|spread|range)\b"),
    "compare": re.compile(r"\b(compare|versus|vs|difference between|differ)\b"),
    "group": re.compile(r"\b(per|by|each|every|across|among|grouped by|split by|broken down by|for each|in each|according to|based on|depending on)\b"),
    "which": re.compile(r"^\s*(which|what|who)\b"),
    "pick": re.compile(r"\b(has|have|had|with|having|gets?|got|recorded|reported|shows?|achiev\w*)\b"),
}
NUM_OPS = [(re.compile(r"\b(over|above|more than|greater than|exceeding|exceeds|higher than|bigger than|larger than|after|since)\b"), "gt"),
           (re.compile(r"\b(at least|no less than|minimum of|from)\b"), "gte"),
           (re.compile(r"\b(under|below|less than|fewer than|lower than|smaller than|before|younger than|cheaper than)\b"), "lt"),
           (re.compile(r"\b(at most|no more than|up to|until|by)\b"), "lte")]


def _measure_for_words(q: str, ctx: QAContext):
    """'most expensive' -> a price-like column; 'oldest/newest' -> a year column (returns column only)."""
    if re.search(r"\b(expensive|cheap\w*|priciest|costly|costliest|price)\b", q):
        for c in ctx.visible("measure"):
            if any(w in c.name.lower() for w in ("price", "cost", "value", "amount", "fee", "salary", "income", "revenue", "sales")):
                return c.name
    if re.search(r"\b(oldest|newest|latest|earliest|recent|youngest)\b", q):
        for c in ctx.visible("year"):
            return c.name
        for c in ctx.visible("measure"):
            if "age" in c.name.lower():
                return c.name
    return None


def build_query(question: str, ctx: QAContext):
    """-> (query dict | None, interpretation text, notes list)"""
    q = " ".join(_clean_q(question).split())
    toks = tokens(question)
    notes = []
    aliases, vocab = build_aliases(ctx)
    ms = find_mentions(toks, ctx, aliases, vocab)
    cols_m = [m for m in ms if m.kind == "col"]
    vals_m = [m for m in ms if m.kind == "value"]
    nums_m = [m for m in ms if m.kind == "number"]
    role = lambda n: ctx.cols[n].role                     # noqa: E731
    cats = [m for m in cols_m if role(m.col) in ("category", "boolean")]
    meas = [m for m in cols_m if role(m.col) in ("measure",)]
    yrs = [m for m in cols_m if role(m.col) in ("year", "date")]
    inferred = _measure_for_words(q, ctx)
    entity_col = next((c.name for c in ctx.cols.values() if c.name.strip().lower() + "s" == ctx.noun), None)
    year_dir = None
    if re.search(r"\b(oldest|earliest)\b", q):
        year_dir = "min"
    elif re.search(r"\b(newest|latest|most recent)\b", q):
        year_dir = "max"
    if inferred and not any(m.col == inferred for m in cols_m) and not meas:
        meas = [Mention(-1, -1, "col", inferred)]
    ymeas = [m for m in cols_m if role(m.col) in ("measure", "year")]

    def filters(exclude_cols=()):
        fs = []
        by_col = {}
        for v in vals_m:
            if v.col not in exclude_cols:
                by_col.setdefault(v.col, []).append(v.value)
        for c, vs in by_col.items():
            fs.append({"column": c, "op": "in" if len(vs) > 1 else "eq", "value": vs if len(vs) > 1 else vs[0]})
        # numeric comparisons: 'over 30000', 'after 2015', 'between 2010 and 2015', 'in 2018'
        text = q
        for m in nums_m:
            col = None
            pre = " ".join(toks[max(0, m.start - 3):m.start])
            cand = [c for c in ymeas if abs(c.start - m.start) <= 4] or ([c for c in ymeas][:1] if len({c.col for c in ymeas}) == 1 else [])
            if cand:
                col = min(cand, key=lambda c: abs(c.start - m.start)).col
            elif 1900 <= m.value <= 2100 and ctx.visible("year"):
                col = ctx.visible("year")[0].name
            if col is None or col in exclude_cols:
                continue
            op = None
            for rx, o in NUM_OPS:
                if rx.search(pre):
                    op = o
                    break
            if "between" in pre and len(nums_m) >= 2:
                continue
            if op is None and re.search(r"\b(in|during|of|for)\b", pre) and 1900 <= m.value <= 2100 and role(col) == "year":
                op = "eq"
            if op:
                fs.append({"column": col, "op": op, "value": m.value})
        bm = re.search(r"between (\d+(?:\.\d+)?)(k?) and (\d+(?:\.\d+)?)(k?)", q)
        if bm:
            lo = float(bm.group(1)) * (1000 if bm.group(2) else 1)
            hi = float(bm.group(3)) * (1000 if bm.group(4) else 1)
            col = ymeas[0].col if ymeas else (ctx.visible("year")[0].name if ctx.visible("year") else None)
            if col and col not in exclude_cols:
                fs += [{"column": col, "op": "gte", "value": lo}, {"column": col, "op": "lte", "value": hi}]
        return fs

    n_top = None
    mt = re.search(r"\btop (\d+)\b|\bbottom (\d+)\b|\b(\d+) (most|least)\b", q)
    if mt:
        n_top = int([g for g in mt.groups() if g and g.isdigit()][0])

    def head_col(cands, after=0):
        """The column a phrase is about: the last column word inside the first noun phrase after `after`."""
        pool = [m for m in cands if m.start >= after] or cands
        if not pool:
            return None
        first = pool[0]
        grp = [first.col]
        last_end = first.end
        for m in pool[1:]:
            if m.start == last_end:                 # adjacent words form one compound noun ('car manufacturer'); 'for', 'of' break it
                grp.append(m.col)
                last_end = m.end
            else:
                break
        return grp[-1]

    has = lambda k: bool(RX[k].search(q))                 # noqa: E731
    agg = "mean" if has("avg") else "median" if has("median") else "sum" if has("sum") else None

    # 1. distinct
    if has("distinct") and (cats or yrs):
        c = (cats or yrs)[-1].col
        return {"intent": "distinct", "column": c, "filters": filters()}, f"How many different values of '{c}' appear.", notes
    # 2. correlation
    if has("corr") and len({m.col for m in ymeas}) >= 2:
        a, b = list(dict.fromkeys(m.col for m in ymeas))[:2]
        return {"intent": "corr", "a": a, "b": b, "filters": filters()}, f"How strongly '{a}' and '{b}' move together (Pearson correlation).", notes
    if has("corr") and len({m.col for m in ymeas}) == 1:
        a = ymeas[0].col
        return {"intent": "corr_all", "a": a, "filters": filters()}, f"How strongly each numeric column moves together with '{a}'.", notes
    # 3. trend
    if has("trend") and (yrs or ctx.visible("year", "date")):
        by = (yrs[0].col if yrs else ctx.visible("year", "date")[0].name)
        m = meas[0].col if meas else None
        return ({"intent": "trend", "measure": m, "by": by, "agg": agg or "mean", "filters": filters()},
                f"{'Average ' + m if m else 'Number of ' + ctx.noun} for each '{by}', and the overall direction.", notes)
    # 4. compare two values
    if has("compare") and len(vals_m) >= 2 and len({v.col for v in vals_m}) == 1 and meas:
        return ({"intent": "compare", "measure": meas[0].col, "by": vals_m[0].col, "values": [v.value for v in vals_m][:2], "agg": agg or "mean"},
                f"Compare {agg or 'mean'} '{meas[0].col}' between {vals_m[0].value} and {vals_m[1].value}.", notes)
    # 5. share / percentage
    if has("percent") and (vals_m or cats):
        if vals_m:
            col = vals_m[0].col
            vs = [v.value for v in vals_m if v.col == col]
            return ({"intent": "share", "column": col, "values": vs, "filters": filters(exclude_cols=(col,))},
                    f"The share of {ctx.noun} where '{col}' is {' or '.join(vs)}.", notes)
        col = head_col(cats)
        return {"intent": "mode", "column": col, "n": n_top or 10, "filters": filters(exclude_cols=(col,)), "as_share": True}, f"The share of {ctx.noun} in each '{col}'.", notes
    # 6. most / least popular
    for key, intent in (("antimode", "antimode"), ("mode", "mode")):
        if has(key):
            pm = RX[key].search(q)
            after = len(tokens(q[:pm.end()]))
            if not cats:
                # 'most popular' of a measure makes no sense: fall back to describing which value is most common
                if yrs:
                    c = yrs[0].col
                    return {"intent": intent, "column": c, "n": n_top or 1, "filters": filters(exclude_cols=(c,))}, f"Which '{c}' value appears in the most {ctx.noun}.", notes
                return None, "I could not tell which column to count. Name the column, for example 'most popular color'.", notes
            c = head_col(cats, after)
            word = "most" if intent == "mode" else "fewest"
            return ({"intent": intent, "column": c, "n": n_top or 1, "filters": filters(exclude_cols=(c,))},
                    f"Which '{c}' has the {word} {ctx.noun} (popularity = number of {ctx.noun} listed).", notes)
    # 7. which <category> has the highest/lowest <agg> <measure>
    ext = "max" if has("max") else "min" if has("min") else None
    if ext and year_dir and any(role(m.col) == "year" for m in ymeas + meas):
        ext = year_dir                                    # oldest = smallest year, newest = largest year
    elif year_dir and not (ymeas + meas):
        ext = year_dir
    if n_top and cats and not meas and not agg:
        c = head_col(cats)
        return {"intent": "mode", "column": c, "n": n_top, "filters": filters(exclude_cols=(c,))}, f"The {n_top} '{c}' values with the most {ctx.noun}.", notes
    if has("which") and ext and cats and meas and (head_col(cats) != entity_col or agg):
        by = head_col(cats)
        a = agg or "mean"
        if not agg:
            notes.append("You did not say 'total' or 'maximum', so I used the average per group. Ask 'total ...' or 'maximum ...' if you meant that.")
        return ({"intent": "agg_by", "measure": meas[0].col, "agg": a, "by": by, "order": "desc" if ext == "max" else "asc", "n": 1, "filters": filters(exclude_cols=(by,))},
                f"Which '{by}' has the {'highest' if ext == 'max' else 'lowest'} {a} '{meas[0].col}'.", notes)
    if has("which") and ext and cats and not meas and not agg and (has("pick") or head_col(cats) != entity_col):
        c = head_col(cats)
        intent = "mode" if ext == "max" else "antimode"
        return {"intent": intent, "column": c, "n": n_top or 1, "filters": filters(exclude_cols=(c,))}, f"Which '{c}' has the {'most' if ext == 'max' else 'fewest'} {ctx.noun}.", notes
    # 8. aggregate by group (the row noun itself, e.g. 'cars', is not a grouping column)
    gcats = cats if has("group") else [m for m in cats if m.col != entity_col]
    if meas and gcats and (has("group") or agg or ext):
        by = head_col(gcats, 0)
        if has("group"):
            gm = RX["group"].search(q)
            after = len(tokens(q[:gm.end()]))
            by = head_col(gcats, after)
        a = agg or ("max" if ext == "max" else "min" if ext == "min" else "mean")
        if not agg and not ext:
            notes.append("You did not say 'total' or 'median', so I used the average. Ask 'total ...' if you meant the sum.")
        return ({"intent": "agg_by", "measure": meas[0].col, "agg": a, "by": by, "order": "desc", "n": n_top, "filters": filters(exclude_cols=(by,))},
                f"The {a} of '{meas[0].col}' for each '{by}'.", notes)
    # 9. most expensive / oldest ... (rows)
    if ext and meas and not agg and (not gcats or re.search(r"\b(car|vehicle|item|product|customer|record|row|one|entry|sale|order)s?\b", q) or inferred):
        return ({"intent": "rows_extreme", "measure": meas[0].col, "which": ext, "n": n_top or 1, "filters": filters()},
                f"The {ctx.noun.rstrip('s') if ctx.noun != 'rows' else 'row'} with the {'highest' if ext == 'max' else 'lowest'} '{meas[0].col}'.", notes)
    # 10. overall aggregate
    if meas and (agg or ext) and not gcats:
        a = agg or ("max" if ext == "max" else "min")
        return {"intent": "agg", "measure": meas[0].col, "agg": a, "filters": filters()}, f"The {a} of '{meas[0].col}'" + (" (with your conditions)." if filters() else "."), notes
    # 11. counts
    if has("count"):
        fs = filters()
        if cats and not fs and (has("group")):
            c = head_col(cats)
            return {"intent": "mode", "column": c, "n": 50, "all": True, "filters": []}, f"How many {ctx.noun} there are for each '{c}'.", notes
        return {"intent": "count", "filters": fs}, f"Number of {ctx.noun}" + (" that match your conditions." if fs else " in total."), notes
    # 12. list / describe / fallbacks
    if has("list") and cats:
        c = head_col(cats)
        return {"intent": "mode", "column": c, "n": 50, "all": True, "filters": filters(exclude_cols=(c,))}, f"Every value of '{c}' with the number of {ctx.noun}.", notes
    if (has("describe") or len(cols_m) == 1) and cols_m:
        c = cols_m[0].col
        return {"intent": "describe", "column": c, "filters": filters(exclude_cols=(c,))}, f"A summary of '{c}'.", notes
    if meas and not cats:
        c = meas[0].col
        return {"intent": "describe", "column": c, "filters": filters()}, f"A summary of '{c}'.", notes
    return None, "", notes


# ====================================================================================== execution
@dataclass
class Answer:
    question: str
    ok: bool
    headline: str = ""
    detail: str = ""
    table: dict | None = None
    interpretation: str = ""
    verified: bool = False
    method: str = "rules"
    notes: list = field(default_factory=list)
    chart: dict | None = None
    query: dict | None = None

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}


def _mask(df, filters):
    m = pd.Series(True, index=df.index)
    for f in filters or []:
        col, op, v = df[f["column"]], f["op"], f["value"]
        if op == "in":
            m &= col.isin(v)
        elif op == "eq":
            m &= (col == v)
        elif op == "gt":
            m &= col > v
        elif op == "gte":
            m &= col >= v
        elif op == "lt":
            m &= col < v
        elif op == "lte":
            m &= col <= v
    return m


def _py_pass(rec, filters):
    for f in filters or []:
        x, op, v = rec[f["column"]], f["op"], f["value"]
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return False
        ok = {"in": lambda: x in v, "eq": lambda: x == v, "gt": lambda: x > v, "gte": lambda: x >= v, "lt": lambda: x < v, "lte": lambda: x <= v}[op]()
        if not ok:
            return False
    return True


def _filter_text(fs):
    parts = []
    sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "=", "in": "in"}
    for f in fs or []:
        v = f["value"]
        v = ", ".join(map(str, v)) if isinstance(v, list) else (f"{v:g}" if isinstance(v, float) else v)
        parts.append(f"{f['column']} {sym[f['op']]} {v}")
    return " and ".join(parts)


def _money(ctx, col, v, extra=0):
    c = ctx.cols[col]
    return fmt_num(c, v, extra)


def execute(query: dict, ctx: QAContext, question: str = "", interpretation: str = "") -> Answer:
    A = Answer(question=question, ok=False, interpretation=interpretation, query=query)
    df = ctx.df
    fs = query.get("filters") or []
    try:
        sub = df[_mask(df, fs)]
    except KeyError as exc:
        A.headline = f"That column was not found: {exc}"
        return A
    recs = [r for r in df.to_dict("records") if _py_pass(r, fs)]
    n, noun = len(sub), ctx.noun
    if len(recs) != n:
        A.headline = "I could not verify the filtered row count, so I am not answering."
        return A
    cond = f" (only {noun} where {_filter_text(fs)})" if fs else ""
    it = query["intent"]
    if n == 0 and it not in ("count",):
        A.ok, A.verified = True, True
        A.headline = f"No {noun} match {_filter_text(fs)}." if fs else "The table has no rows."
        return A

    if it in ("mode", "antimode"):
        col = query["column"]
        vc = sub[col].dropna().value_counts()
        rows = sorted(((str(k), int(v)) for k, v in vc.items()), key=lambda kv: (-kv[1], kv[0].casefold()))
        py = sorted(Counter(r[col] for r in recs if isinstance(r[col], str)).items(), key=lambda kv: (-kv[1], kv[0].casefold()))
        A.verified = rows == py
        total = sum(v for _, v in rows)
        if not rows:
            A.headline = f"'{col}' has no values."
            return A
        target = max(v for _, v in rows) if it == "mode" else min(v for _, v in rows)
        winners = [k for k, v in rows if v == target]
        k_top = query.get("n", 1)
        if query.get("as_share"):
            A.headline = f"{rows[0][0]} is the largest group: {rows[0][1] / total:.1%} of {noun}"
        elif len(winners) == 1:
            A.headline = winners[0]
            A.detail = f"{target} of {total} {noun} ({target / total:.1%}){cond}."
        else:
            A.headline = ", ".join(winners[:6]) + (" ..." if len(winners) > 6 else "")
            unit = (noun[:-1] if noun.endswith("s") and noun != "rows" else "row") if target == 1 else noun
            A.detail = f"It is a tie: {len(winners)} values have {target} {unit} each ({target / total:.1%} each){cond}."
        if query.get("all"):
            A.headline = f"{len(rows)} different values"
            A.detail = f"Every value of {col} with the number of {noun}{cond}."
        elif k_top and k_top > 1 and not query.get("as_share"):
            ordered = rows if it == "mode" else sorted(rows, key=lambda kv: (kv[1], kv[0].casefold()))
            A.headline = ", ".join(k for k, _ in ordered[:k_top])
            A.detail = f"Top {k_top} by number of {noun}{cond}."
        show = rows if it == "mode" else sorted(rows, key=lambda kv: (kv[1], kv[0].casefold()))
        A.table = {"columns": [col, noun.title() if noun != "rows" else "Rows", "% of total"], "fmt": ["text", "int", "pct"],
                   "rows": [[k, v, v / total] for k, v in show[:50]]}
        A.chart = {"type": "bar", "title": f"{noun.title() if noun != 'rows' else 'Rows'} by {col}", "x": 0, "y": [1]}
        A.ok = A.verified
        if not A.verified:
            A.headline, A.detail = "The two calculations disagreed, so no answer is given.", ""
        return A

    if it == "agg_by":
        m, by, a = query["measure"], query["by"], query["agg"]
        g = sub.dropna(subset=[by])
        func = {"mean": "mean", "median": "median", "sum": "sum", "min": "min", "max": "max"}[a]
        res = g.groupby(by)[m].agg([func, "count"])
        res = res[res["count"] > 0]
        py = {}
        for r in recs:
            if isinstance(r[by], str) and r[m] is not None and not (isinstance(r[m], float) and math.isnan(r[m])):
                py.setdefault(r[by], []).append(float(r[m]))
        pyv = {k: {"mean": py_mean, "median": lambda x: float(np.median(x)), "sum": math.fsum, "min": min, "max": max}[a](v) for k, v in py.items()}
        ok = set(pyv) == set(res.index.astype(str)) and all(close(float(res.loc[k, func]), pyv[str(k)]) for k in res.index)
        A.verified = ok
        order = query.get("order", "desc")
        rows = sorted(((str(k), float(r[func]), int(r["count"])) for k, r in res.iterrows()), key=lambda t: (-t[1] if order == "desc" else t[1], t[0].casefold()))
        if not rows:
            A.headline = "There are no values to calculate."
            return A
        k_top = query.get("n")
        A.table = {"columns": [by, f"{a.title()} of {m}", noun.title() if noun != "rows" else "Rows"], "fmt": ["text", f"num:{m}", "int"],
                   "rows": [[k, v, c] for k, v, c in rows]}
        if k_top == 1:
            best = rows[0]
            tied = [r for r in rows if close(r[1], best[1])]
            A.headline = ", ".join(t[0] for t in tied[:5])
            A.detail = f"{a} {m}: {_money(ctx, m, best[1], 1 if a in ('mean', 'median') else 0)} ({plural(best[2], noun[:-1] if noun.endswith('s') and noun != 'rows' else 'row')} in that group){cond}."
            if min(r[2] for r in tied) < 5:
                A.notes.append("This group has fewer than 5 rows, so the result is not very reliable.")
        else:
            A.headline = f"{a.title()} {m} for each {by}"
            A.detail = f"Highest: {rows[0][0]} ({_money(ctx, m, rows[0][1], 1 if a in ('mean', 'median') else 0)}). Lowest: {rows[-1][0]} ({_money(ctx, m, rows[-1][1], 1 if a in ('mean', 'median') else 0)}){cond}."
        A.chart = {"type": "bar", "title": f"{a.title()} {m} by {by}", "x": 0, "y": [1]}
        A.ok = ok
        if not ok:
            A.headline, A.detail, A.table = "The two calculations disagreed, so no answer is given.", "", None
        return A

    if it == "agg":
        m, a = query["measure"], query["agg"]
        xs = [float(x) for x in sub[m].dropna()]
        if not xs:
            A.headline = f"'{m}' has no values{cond}."
            return A
        val = {"mean": float(np.mean(xs)), "median": float(np.median(xs)), "sum": math.fsum(xs), "min": min(xs), "max": max(xs)}[a]
        pyx = [float(r[m]) for r in recs if r[m] is not None and not (isinstance(r[m], float) and math.isnan(r[m]))]
        pyv = {"mean": py_mean, "median": lambda x: float(np.median(x)), "sum": math.fsum, "min": min, "max": max}[a](pyx)
        A.verified = close(val, pyv)
        A.headline = _money(ctx, m, val, 1 if a in ("mean", "median") else 0)
        A.detail = f"The {a} of {m} across {len(xs)} {noun}{cond}."
        A.table = {"columns": ["Measure", "Value"], "fmt": ["text", f"num:{m}"], "rows": [[f"{a} {m}", val]]}
        A.ok = A.verified
        return A

    if it == "count":
        pyn = len(recs)
        A.verified = (pyn == n)
        A.headline = f"{n:,}"
        A.detail = (f"{noun} where {_filter_text(fs)}." if fs else f"{noun} in total.")
        A.ok = A.verified
        return A

    if it == "share":
        col, vs = query["column"], query["values"]
        hit = int(sub[col].isin(vs).sum())
        pyhit = sum(1 for r in recs if r[col] in vs)
        A.verified = hit == pyhit
        A.headline = f"{hit / n:.1%}" if n else "-"
        A.detail = f"{hit} of {n} {noun} have {col} = {' or '.join(vs)}{cond if fs else ''}."
        A.table = {"columns": ["Group", noun.title() if noun != "rows" else "Rows", "% of total"], "fmt": ["text", "int", "pct"],
                   "rows": [[" or ".join(vs), hit, hit / n], ["All others", n - hit, (n - hit) / n]]}
        A.ok = A.verified
        return A

    if it == "distinct":
        col = query["column"]
        k = int(sub[col].nunique(dropna=True))
        pyk = len({r[col] for r in recs if r[col] is not None and not (isinstance(r[col], float) and math.isnan(r[col]))})
        A.verified = k == pyk
        A.headline = f"{k:,}"
        A.detail = f"different values of {col} among {n} {noun}{cond}."
        A.ok = A.verified
        return A

    if it in ("corr", "corr_all"):
        pairs = [(query["a"], query["b"])] if it == "corr" else [(query["a"], c.name) for c in ctx.visible("measure", "year") if c.name != query["a"]]
        rows, ok_all = [], True
        for a, b in pairs:
            d = sub[[a, b]].dropna()
            if len(d) < 3:
                continue
            xs, ys = [float(x) for x in d[a]], [float(y) for y in d[b]]
            r = py_pearson(xs, ys)
            if r is None:
                continue
            rn = float(np.corrcoef(xs, ys)[0, 1])
            ok_all &= close(r, rn, rel=1e-9, abs_=1e-9)
            lab = "strong" if abs(r) >= 0.7 else "moderate" if abs(r) >= 0.4 else "weak" if abs(r) >= 0.2 else "negligible"
            rows.append([a, b, r, len(xs), f"{lab} {'positive' if r > 0 else 'negative'}"])
        if not rows:
            A.headline = "There are not enough rows with both values to calculate a correlation (at least 3 are needed)."
            return A
        rows.sort(key=lambda t: -abs(t[2]))
        top = rows[0]
        A.verified = ok_all
        A.headline = f"r = {top[2]:.2f} ({top[4]})"
        A.detail = (f"Between {top[0]} and {top[1]} across {top[3]} {noun}. A correlation shows a pattern, not that one thing causes the other."
                    + (" With fewer than 10 rows it is not reliable." if top[3] < 10 else ""))
        A.table = {"columns": ["Column A", "Column B", "Correlation (r)", "Rows used", "Strength"], "fmt": ["text", "text", "r", "int", "text"], "rows": rows}
        A.ok = ok_all
        return A

    if it == "trend":
        by, m, a = query["by"], query.get("measure"), query.get("agg", "mean")
        if ctx.cols[by].role == "date":
            per = sub[by].dt.to_period("Y").astype(str)
            keys = per
        else:
            keys = sub[by]
        d = pd.DataFrame({"k": keys, "m": sub[m] if m else 1}).dropna(subset=["k"])
        if m:
            d = d.dropna(subset=["m"])
            g = d.groupby("k")["m"].agg(["mean" if a == "mean" else a, "count"])
            rows = [[str(int(k)) if isinstance(k, (int, float, np.integer, np.floating)) else str(k), float(r.iloc[0]), int(r.iloc[1])] for k, r in g.sort_index().iterrows()]
        else:
            g = d.groupby("k").size()
            rows = [[str(int(k)) if isinstance(k, (int, float, np.integer, np.floating)) else str(k), int(v), int(v)] for k, v in g.sort_index().items()]
        slope_txt, ok = "", True
        if ctx.cols[by].role == "year" and m:
            dd = sub[[by, m]].dropna()
            if len(dd) >= 5 and dd[by].nunique() >= 3:
                xs, ys = [float(x) for x in dd[by]], [float(y) for y in dd[m]]
                s1, s2 = py_slope(xs, ys), float(np.polyfit(xs, ys, 1)[0])
                ok = close(s1, s2, rel=1e-7, abs_=1e-7)
                r_ = py_pearson(xs, ys)
                slope_txt = f"On average {m} changes by {_money(ctx, m, s1, 1)} per {by} step (r = {r_:.2f}). "
        if not rows:
            A.headline = "There is not enough data to show a trend."
            return A
        A.verified = ok
        A.headline = f"{rows[0][0]} to {rows[-1][0]}: {_money(ctx, m, rows[0][1], 1) if m else rows[0][1]} -> {_money(ctx, m, rows[-1][1], 1) if m else rows[-1][1]}"
        A.detail = slope_txt + "A trend describes the pattern in these rows; it does not prove the reason."
        A.table = {"columns": [by, (f"{'Average ' if a == 'mean' else a.title() + ' '}{m}" if m else noun.title() if noun != "rows" else "Rows"), "Rows"],
                   "fmt": ["text", f"num:{m}" if m else "int", "int"], "rows": rows}
        A.chart = {"type": "line", "title": f"{m or noun.title()} by {by}", "x": 0, "y": [1]}
        A.ok = ok
        return A

    if it == "rows_extreme":
        m, which, k = query["measure"], query["which"], query.get("n", 1)
        d = sub.dropna(subset=[m])
        ordered = d.sort_values(m, ascending=(which == "min"), kind="mergesort")
        best = ordered[m].iloc[0] if len(ordered) else None
        py_best = (max if which == "max" else min)(float(r[m]) for r in recs if r[m] is not None and not (isinstance(r[m], float) and math.isnan(r[m])))
        A.verified = close(float(best), py_best)
        tied = ordered[ordered[m] == best]
        keep = [c.name for c in ctx.visible() if c.role in ("category", "id", "year", "measure", "boolean", "text")][:10]
        if m not in keep:
            keep.append(m)
        show = (tied if len(tied) > 1 or k == 1 else ordered.head(k))[keep]
        value_txt = str(int(best)) if ctx.cols[m].role == "year" else _money(ctx, m, float(best))
        ent = next((c.name for c in ctx.visible() if c.name.strip().lower() + "s" == noun), None)
        if ent and len(tied) == 1:
            A.headline = f"{tied[ent].iloc[0]} ({value_txt})"
        elif ent:
            A.headline = f"{len(tied)} {noun} tie at {value_txt}"
        else:
            A.headline = value_txt
        A.detail = f"{'Highest' if which == 'max' else 'Lowest'} {m}: {value_txt}{cond}" + (f" - {len(tied)} {noun} are tied." if len(tied) > 1 else ".")
        A.table = {"columns": keep, "fmt": [(f"num:{c}" if ctx.cols[c].role == "measure" else "text") for c in keep],
                   "rows": [[(None if pd.isna(v) else (float(v) if ctx.cols[c].role == "measure" else (str(int(v)) if ctx.cols[c].role == "year" else str(v)))) for c, v in zip(keep, r)] for r in show.itertuples(index=False)]}
        A.ok = A.verified
        return A

    if it == "compare":
        m, by, vals, a = query["measure"], query["by"], query["values"], query.get("agg", "mean")
        rows, ok = [], True
        for v in vals:
            xs = [float(x) for x in sub[sub[by] == v][m].dropna()]
            pyx = [float(r[m]) for r in recs if r[by] == v and r[m] is not None and not (isinstance(r[m], float) and math.isnan(r[m]))]
            if not xs:
                continue
            val = {"mean": float(np.mean(xs)), "median": float(np.median(xs)), "sum": math.fsum(xs)}[a]
            ok &= close(val, {"mean": py_mean, "median": lambda x: float(np.median(x)), "sum": math.fsum}[a](pyx))
            rows.append([v, val, len(xs)])
        if len(rows) < 2:
            A.headline = "One of the groups has no values."
            return A
        diff = rows[0][1] - rows[1][1]
        A.verified = ok
        A.headline = f"{rows[0][0]}: {_money(ctx, m, rows[0][1], 1)} vs {rows[1][0]}: {_money(ctx, m, rows[1][1], 1)}"
        A.detail = f"Difference ({rows[0][0]} minus {rows[1][0]}): {_money(ctx, m, diff, 1)}."
        A.table = {"columns": [by, f"{a.title()} of {m}", "Rows"], "fmt": ["text", f"num:{m}", "int"], "rows": rows}
        A.ok = ok
        return A

    if it == "describe":
        col = query["column"]
        c = ctx.cols[col]
        if c.role in ("measure", "year"):
            xs = [float(x) for x in sub[col].dropna()]
            if not xs:
                A.headline = f"'{col}' has no values."
                return A
            q1, q3 = float(np.percentile(xs, 25)), float(np.percentile(xs, 75))
            pyq1 = sorted(xs)[0]
            A.verified = close(float(np.mean(xs)), py_mean(xs)) and close(math.fsum(xs), float(np.sum(xs)))
            fm = (lambda v: str(int(v))) if c.role == "year" else (lambda v: _money(ctx, col, v))
            A.headline = f"average {fm(float(np.mean(xs)))}, median {fm(float(np.median(xs)))}"
            A.detail = f"{len(xs)} values from {fm(min(xs))} to {fm(max(xs))}{cond}."
            A.table = {"columns": ["Statistic", "Value"], "fmt": ["text", f"num:{col}"],
                       "rows": [["Count", float(len(xs))], ["Mean", float(np.mean(xs))], ["Median", float(np.median(xs))], ["Lowest", min(xs)], ["25th percentile", q1],
                                ["75th percentile", q3], ["Highest", max(xs)], ["Sum", math.fsum(xs)]]}
            A.ok = A.verified
            return A
        return execute({"intent": "mode", "column": col, "n": 50, "all": True, "filters": fs}, ctx, question, interpretation)

    A.headline = "I do not know how to answer that yet."
    return A


# ====================================================================================== public API
EXAMPLES = ["What is the most popular value of a category column? e.g. 'most popular color'",
            "What is the average of a number column by a category? e.g. 'average price per owner'",
            "How many rows match? e.g. 'how many automatic cars are there?'",
            "Is one number related to another? e.g. 'is price related to km driven?'"]


def ask(question: str, ctx: QAContext, *, use_ai: bool = True) -> Answer:
    question = question.strip()
    if not question:
        return Answer(question="", ok=False, headline="Type a question first.")
    try:
        query, interp, notes = build_query(question, ctx)
    except Exception:                                   # a parsing bug must never break the page
        query, interp, notes = None, "", []
    method = "rules"
    if query is None and use_ai and ai_enabled():
        query, interp, why = ai_translate(question, ctx)
        method = "ai"
        notes = ([why] if why else [])
    if query is None:
        cats = [c.name for c in ctx.visible("category")]
        nums = [c.name for c in ctx.visible("measure")]
        ex = []
        if cats:
            ex.append(f"most popular {cats[0]}")
        if nums and cats:
            ex.append(f"average {nums[0]} per {cats[-1]}")
        if len(nums) >= 2:
            ex.append(f"is {nums[0]} related to {nums[1]}")
        ex.append(f"how many {ctx.noun} are there")
        return Answer(question=question, ok=False, headline="I could not work out which columns or calculation you mean.",
                      detail="The columns are: " + ", ".join(c.name for c in ctx.visible()) + ". Try questions like: " + "; ".join(f"'{e}'" for e in ex) + ".")
    ans = execute(query, ctx, question, interp)
    ans.method = method
    ans.notes = list(notes) + ans.notes
    if method == "ai":
        ans.notes.append("An AI model translated your question into a calculation; the numbers themselves were calculated and double-checked by this tool.")
    return ans


# ====================================================================================== optional AI translation
def ai_enabled() -> bool:
    return os.environ.get("SWA_AI_ENABLED", "") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))


def schema_summary(ctx: QAContext) -> dict:
    """What the AI is allowed to see: names, types, tiny summaries. Never rows; never personal-data columns."""
    out = []
    for c in ctx.visible():
        s = ctx.df[c.name]
        d = {"name": c.name, "type": c.role}
        if c.role in ("category", "boolean"):
            d["top_values"] = [str(v) for v in s.value_counts().index[:8]]
        elif c.role in ("measure", "year") and s.notna().any():
            d["min"], d["max"] = float(s.min()), float(s.max())
        out.append(d)
    return {"row_count": len(ctx.df), "row_noun": ctx.noun, "columns": out}


AI_INSTRUCTIONS = (
    "You translate a user's question about a table into ONE JSON object. Output ONLY JSON, no prose. "
    "Allowed intents: mode, antimode (column, n), agg (measure, agg), agg_by (measure, agg, by, order asc|desc, n), count, share (column, values), "
    "corr (a, b), corr_all (a), trend (by, measure|null, agg), distinct (column), describe (column), rows_extreme (measure, which max|min, n), compare (measure, by, values[2], agg). "
    "agg is one of mean, median, sum, min, max. Optional 'filters': list of {column, op (eq|in|gt|gte|lt|lte), value}. "
    "Use ONLY column names from the schema, exactly as written. If the question cannot be answered from the table return {\"intent\": \"none\", \"why\": \"...\"}.")


def validate_query(q, ctx: QAContext):
    if not isinstance(q, dict) or q.get("intent") not in INTENTS:
        return None
    names = {c.name: c for c in ctx.visible()}
    out = {"intent": q["intent"]}

    def col(key, roles=None):
        v = q.get(key)
        if v not in names or (roles and names[v].role not in roles):
            raise ValueError(key)
        return v
    try:
        it = q["intent"]
        if it in ("mode", "antimode"):
            out["column"] = col("column", ("category", "boolean", "year")); out["n"] = max(1, min(int(q.get("n", 1)), 50))
        elif it == "agg":
            out["measure"] = col("measure", ("measure",)); out["agg"] = q["agg"] if q.get("agg") in AGGS else "mean"
        elif it == "agg_by":
            out["measure"] = col("measure", ("measure",)); out["by"] = col("by", ("category", "boolean", "year"))
            out["agg"] = q["agg"] if q.get("agg") in AGGS else "mean"; out["order"] = "asc" if q.get("order") == "asc" else "desc"
            out["n"] = q.get("n") if isinstance(q.get("n"), int) else None
        elif it == "share":
            out["column"] = col("column", ("category", "boolean")); vs = q.get("values")
            if not isinstance(vs, list) or not vs or any(v not in set(ctx.df[out["column"]].dropna()) for v in vs):
                raise ValueError("values")
            out["values"] = vs
        elif it == "corr":
            out["a"] = col("a", ("measure", "year")); out["b"] = col("b", ("measure", "year"))
        elif it == "corr_all":
            out["a"] = col("a", ("measure", "year"))
        elif it == "trend":
            out["by"] = col("by", ("year", "date")); out["measure"] = col("measure", ("measure",)) if q.get("measure") else None
            out["agg"] = q["agg"] if q.get("agg") in AGGS else "mean"
        elif it in ("distinct", "describe"):
            out["column"] = col("column")
        elif it == "rows_extreme":
            out["measure"] = col("measure", ("measure", "year")); out["which"] = "min" if q.get("which") == "min" else "max"; out["n"] = max(1, min(int(q.get("n", 1)), 20))
        elif it == "compare":
            out["measure"] = col("measure", ("measure",)); out["by"] = col("by", ("category", "boolean"))
            vs = q.get("values")
            if not isinstance(vs, list) or len(vs) != 2 or any(v not in set(ctx.df[out["by"]].dropna()) for v in vs):
                raise ValueError("values")
            out["values"] = vs; out["agg"] = q["agg"] if q.get("agg") in ("mean", "median", "sum") else "mean"
        fs = []
        for f in q.get("filters") or []:
            c = col_name = f.get("column")
            if col_name not in names or f.get("op") not in OPS | {"in"}:
                raise ValueError("filter")
            v = f.get("value")
            if f["op"] == "in":
                if not isinstance(v, list) or not v:
                    raise ValueError("filter")
            elif names[col_name].role in ("measure", "year"):
                v = float(v)
            else:
                v = str(v)
            fs.append({"column": col_name, "op": f["op"], "value": v})
        out["filters"] = fs
    except (ValueError, KeyError, TypeError):
        return None
    return out


def ai_translate(question: str, ctx: QAContext):
    """-> (query|None, interpretation, note). Sends only column names + summary statistics."""
    body = {"model": os.environ.get("SWA_AI_MODEL", "claude-haiku-5-5"), "max_tokens": 500, "system": AI_INSTRUCTIONS,
            "messages": [{"role": "user", "content": json.dumps({"schema": schema_summary(ctx), "question": question[:500]})}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
    try:
        with _opener()(req, timeout=25) as r:
            data = json.loads(r.read().decode())
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        raw = json.loads(text)
    except Exception:
        return None, "", "The AI helper could not be reached."
    if isinstance(raw, dict) and raw.get("intent") == "none":
        return None, "", str(raw.get("why", ""))[:200]
    q = validate_query(raw, ctx)
    if q is None:
        return None, "", "The AI suggested a calculation that did not fit your columns, so it was ignored."
    return q, f"AI interpretation: {q['intent']} (checked against your columns).", ""


def _opener():
    return urllib.request.urlopen
