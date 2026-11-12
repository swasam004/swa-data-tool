"""Configuration loading and strict validation.

Every unknown key is rejected on purpose: a typo such as ``reqired: true``
would otherwise silently switch a safety check off.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

import yaml

DEFAULT_MISSING_TOKENS = ["", "na", "n/a", "nan", "null", "none", "-", "--"]
DEFAULT_CURRENCY_SYMBOLS = ["KES", "KSh", "Ksh", "Kshs", "USD", "UGX", "TZS", "$", "£", "€"]
COLUMN_TYPES = {"text", "category", "number", "date", "multiselect"}
AGGS = {"sum", "mean", "count", "count_rows", "count_unique", "min", "max", "median"}
ADDITIVE_AGGS = {"sum", "count", "count_rows"}
CHARTS = {"column", "column_stacked", "bar", "line", "pie", "none"}
SORTS = {"label", "value_desc", "value_asc"}
CASES = {"keep", "lower", "upper", "title"}
PII_MODES = {"hash", "mask", "drop"}
DERIVED_SUFFIXES = (" (Year)", " (Quarter)", " (Month)")
INDICATOR_TYPES = {"count", "count_unique", "sum", "mean", "percent"}
CAT_OPS = {"equals", "not_equals", "in", "not_in"}
NUM_OPS = {"equals", "not_equals", "in", "not_in", "gte", "gt", "lte", "lt"}
ALL_OPS = CAT_OPS | NUM_OPS

TOP_KEYS = {
    "dataset_name", "organisation", "folders", "file_patterns", "read", "missing_tokens",
    "currency_symbols", "max_quarantine_pct", "excel_formulas_max_rows", "privacy_salt",
    "columns", "pivots", "indicators",
}
FOLDER_KEYS = {"inbox", "outputs"}
READ_KEYS = {"encoding", "delimiter", "excel_sheet", "header_row"}
COLUMN_KEYS = {
    "name", "type", "required", "unique", "case", "mapping", "allowed",
    "integer", "min", "max", "decimals", "decimal_separator",
    "thousands_separator", "date_formats", "min_date", "max_date",
    "source", "aliases", "pii", "bands", "options", "separator",
}
BAND_KEYS = {"name", "edges", "labels"}
PIVOT_KEYS = {"name", "rows", "columns", "values", "agg", "chart", "sort", "percent_of"}
INDICATOR_KEYS = {"name", "description", "type", "where", "among", "values", "unique_by", "disaggregate_by"}


class ConfigError(Exception):
    """The configuration file is invalid. The message says exactly what to fix."""


def _as_date(value, where: str) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
    raise ConfigError(f"{where}: must be a date written as YYYY-MM-DD, got {value!r}")


def _check_keys(section: dict, allowed: set, where: str) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown setting(s) {unknown}. Allowed: {sorted(allowed)}")


def _no_yaml_surprise(value, where: str):
    """YAML turns unquoted yes/no/on/off/true/false/null into booleans or None. Refuse, never guess."""
    if isinstance(value, bool) or value is None:
        raise ConfigError(
            f"{where}: the value {value!r} was read by YAML as true/false/empty, not as text. "
            "Put quotes around it, e.g.  \"Yes\"  or  \"No\"  (also for keys such as \"yes\": Yes).")
    return value


def _stringify_keys(obj):
    if isinstance(obj, dict):
        return {str(k): _stringify_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_keys(v) for v in obj]
    return obj


def _num(value, where: str):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: must be a number, got {value!r}")
    return float(value)


def derived_column_names(date_col: str) -> list[str]:
    return [date_col + s for s in DERIVED_SUFFIXES]


def band_labels(edges: list, integer: bool) -> list[str]:
    out = []
    for i, lo in enumerate(edges):
        if i == len(edges) - 1:
            out.append(f"{lo:g}+")
        elif integer:
            out.append(f"{lo:g}-{edges[i + 1] - 1:g}")
        else:
            out.append(f"{lo:g} to <{edges[i + 1]:g}")
    return out


def load_config(path) -> dict:
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} must contain a YAML mapping at the top level.")
    cfg = normalize_config(raw, base_dir=path.parent)
    cfg["_config_path"] = str(path)
    public = {k: v for k, v in raw.items() if k != "privacy_salt"}   # never fingerprint the secret
    cfg["_config_sha256"] = hashlib.sha256(json.dumps(_stringify_keys(public), sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return cfg


def normalize_config(raw: dict, base_dir: Path) -> dict:
    _check_keys(raw, TOP_KEYS, "config")
    base_dir = Path(base_dir)
    cfg: dict = {}
    cfg["dataset_name"] = str(raw.get("dataset_name") or "Dataset").strip() or "Dataset"
    cfg["organisation"] = str(raw.get("organisation") or "").strip()

    folders = raw.get("folders") or {}
    if not isinstance(folders, dict):
        raise ConfigError("folders: must be a mapping with 'inbox' and 'outputs'.")
    _check_keys(folders, FOLDER_KEYS, "folders")
    cfg["inbox"] = str((base_dir / str(folders.get("inbox", "inbox"))).resolve())
    cfg["outputs"] = str((base_dir / str(folders.get("outputs", "outputs"))).resolve())

    patterns = raw.get("file_patterns", ["*.csv", "*.xlsx", "*.json"])
    if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) for p in patterns):
        raise ConfigError("file_patterns: must be a non-empty list of patterns such as ['*.csv'].")
    cfg["file_patterns"] = patterns

    read = raw.get("read") or {}
    if not isinstance(read, dict):
        raise ConfigError("read: must be a mapping.")
    _check_keys(read, READ_KEYS, "read")
    header_row = read.get("header_row", 0)
    if isinstance(header_row, bool) or not isinstance(header_row, int) or header_row < 0:
        raise ConfigError("read.header_row: must be a whole number >= 0 (0 = first row).")
    delimiter = str(read.get("delimiter", ","))
    if len(delimiter) != 1:
        raise ConfigError("read.delimiter: must be a single character such as ',' or ';'.")
    cfg["read"] = {"encoding": str(read.get("encoding", "utf-8-sig")), "delimiter": delimiter,
                   "excel_sheet": read.get("excel_sheet", 0), "header_row": header_row}

    tokens = raw.get("missing_tokens", DEFAULT_MISSING_TOKENS)
    if not isinstance(tokens, list) or not all(isinstance(t, (str, int, float)) for t in tokens):
        raise ConfigError("missing_tokens: must be a list of text values.")
    for t in tokens:
        _no_yaml_surprise(t, "missing_tokens")
    cfg["missing_tokens"] = sorted({str(t).strip().casefold() for t in tokens} | {""})

    symbols = raw.get("currency_symbols", DEFAULT_CURRENCY_SYMBOLS)
    if not isinstance(symbols, list) or not all(isinstance(s, str) and s for s in symbols):
        raise ConfigError("currency_symbols: must be a list of non-empty text values.")
    cfg["currency_symbols"] = sorted(symbols, key=len, reverse=True)

    pct = _num(raw.get("max_quarantine_pct", 10), "max_quarantine_pct")
    if pct is None or not (0 <= pct <= 100):
        raise ConfigError("max_quarantine_pct: must be between 0 and 100.")
    cfg["max_quarantine_pct"] = pct

    max_rows = raw.get("excel_formulas_max_rows", 20000)
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 0:
        raise ConfigError("excel_formulas_max_rows: must be a whole number >= 0.")
    cfg["excel_formulas_max_rows"] = max_rows
    cfg["privacy_salt"] = str(raw.get("privacy_salt") or "")

    # ---- columns -------------------------------------------------------
    raw_cols = raw.get("columns")
    if not isinstance(raw_cols, list) or not raw_cols:
        raise ConfigError("columns: you must list at least one column.")
    columns, seen = [], set()
    for i, rc in enumerate(raw_cols, start=1):
        where = f"columns[{i}]"
        if not isinstance(rc, dict):
            raise ConfigError(f"{where}: must be a mapping with at least 'name' and 'type'.")
        _check_keys(rc, COLUMN_KEYS, where)
        name = rc.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{where}: 'name' is required.")
        name = name.strip()
        where = f"column '{name}'"
        if name.casefold() in seen:
            raise ConfigError(f"{where}: duplicate column name (names are case-insensitive).")
        seen.add(name.casefold())
        if name == "Source Row":
            raise ConfigError(f"{where}: 'Source Row' is reserved by the tool.")
        ctype = rc.get("type")
        if ctype not in COLUMN_TYPES:
            raise ConfigError(f"{where}: type must be one of {sorted(COLUMN_TYPES)}, got {ctype!r}.")
        col = {
            "name": name, "type": ctype,
            "required": bool(rc.get("required", False)), "unique": bool(rc.get("unique", False)),
            "case": "keep", "mapping": {}, "allowed": None,
            "integer": False, "min": None, "max": None, "decimals": None,
            "decimal_separator": ".", "thousands_separator": ",",
            "date_formats": [], "min_date": None, "max_date": None,
            "source": name, "aliases": [], "pii": None, "bands": None,
            "options": [], "separator": " ",
        }
        for flag in ("required", "unique", "integer"):
            if flag in rc and not isinstance(rc[flag], bool):
                raise ConfigError(f"{where}: '{flag}' must be true or false.")
        # header aliases (e.g. KoboToolbox 'group_name/question')
        if "source" in rc:
            if not isinstance(rc["source"], str) or not rc["source"].strip():
                raise ConfigError(f"{where}: 'source' must be the exact header text in your file.")
            col["source"] = rc["source"].strip()
        if "aliases" in rc:
            if not isinstance(rc["aliases"], list) or not all(isinstance(a, str) and a.strip() for a in rc["aliases"]):
                raise ConfigError(f"{where}: 'aliases' must be a list of header texts.")
            col["aliases"] = [a.strip() for a in rc["aliases"]]
        if "pii" in rc:
            if rc["pii"] not in PII_MODES:
                raise ConfigError(f"{where}: pii must be one of {sorted(PII_MODES)}.")
            if ctype != "text":
                raise ConfigError(f"{where}: pii protection applies to 'text' columns (names, phone numbers, ID numbers).")
            col["pii"] = rc["pii"]

        text_like = ("text", "category")
        groups = {
            "case": text_like, "mapping": text_like, "allowed": text_like,
            "integer": ("number",), "min": ("number",), "max": ("number",), "decimals": ("number",),
            "decimal_separator": ("number",), "thousands_separator": ("number",), "bands": ("number",),
            "date_formats": ("date",), "min_date": ("date",), "max_date": ("date",),
            "options": ("multiselect",), "separator": ("multiselect",),
        }
        for k, types in groups.items():
            if k in rc and ctype not in types:
                raise ConfigError(f"{where}: '{k}' only applies to {'/'.join(types)} columns.")

        if ctype in text_like:
            case = rc.get("case", "keep")
            if case not in CASES:
                raise ConfigError(f"{where}: case must be one of {sorted(CASES)}.")
            col["case"] = case
            mapping = rc.get("mapping") or {}
            if not isinstance(mapping, dict):
                raise ConfigError(f"{where}: mapping must be a mapping of old value: new value.")
            norm_map = {}
            for k, v in mapping.items():
                _no_yaml_surprise(k, f"{where}.mapping key")
                _no_yaml_surprise(v, f"{where}.mapping value for {k!r}")
                key = " ".join(str(k).split()).casefold()
                if key in norm_map and norm_map[key] != str(v):
                    raise ConfigError(f"{where}: mapping has conflicting entries for {k!r}.")
                norm_map[key] = str(v)
            col["mapping"] = norm_map
            allowed = rc.get("allowed")
            if allowed is not None:
                if not isinstance(allowed, list) or not allowed:
                    raise ConfigError(f"{where}: allowed must be a non-empty list.")
                col["allowed"] = [str(_no_yaml_surprise(a, f"{where}.allowed")) for a in allowed]
        if ctype == "number":
            col["integer"] = bool(rc.get("integer", False))
            col["min"] = _num(rc.get("min"), f"{where}.min")
            col["max"] = _num(rc.get("max"), f"{where}.max")
            if col["min"] is not None and col["max"] is not None and col["min"] > col["max"]:
                raise ConfigError(f"{where}: min is greater than max.")
            dec = rc.get("decimals")
            if dec is not None and (isinstance(dec, bool) or not isinstance(dec, int) or not 0 <= dec <= 10):
                raise ConfigError(f"{where}: decimals must be a whole number from 0 to 10.")
            col["decimals"] = dec if dec is not None else (0 if col["integer"] else 2)
            col["decimal_separator"] = str(rc.get("decimal_separator", "."))
            col["thousands_separator"] = str(rc.get("thousands_separator", ","))
            if len(col["decimal_separator"]) != 1:
                raise ConfigError(f"{where}: decimal_separator must be one character.")
            if len(col["thousands_separator"]) > 1:
                raise ConfigError(f"{where}: thousands_separator must be one character or empty.")
            if col["decimal_separator"] == col["thousands_separator"]:
                raise ConfigError(f"{where}: decimal and thousands separators must differ.")
            if rc.get("bands") is not None:
                b = rc["bands"]
                if not isinstance(b, dict):
                    raise ConfigError(f"{where}: bands must be a mapping with 'name' and 'edges'.")
                _check_keys(b, BAND_KEYS, f"{where}.bands")
                bname = b.get("name")
                edges = b.get("edges")
                if not isinstance(bname, str) or not bname.strip():
                    raise ConfigError(f"{where}.bands: 'name' is required (e.g. 'Age Group').")
                if (not isinstance(edges, list) or len(edges) < 2
                        or not all(isinstance(e, (int, float)) and not isinstance(e, bool) for e in edges)
                        or any(edges[k] >= edges[k + 1] for k in range(len(edges) - 1))):
                    raise ConfigError(f"{where}.bands: 'edges' must be 2+ numbers in strictly increasing order, e.g. [0, 5, 15, 25].")
                labels = b.get("labels")
                if labels is None:
                    labels = band_labels(edges, col["integer"])
                elif (not isinstance(labels, list) or len(labels) != len(edges)
                      or len({str(x) for x in labels}) != len(labels)):
                    raise ConfigError(f"{where}.bands: 'labels' must have exactly {len(edges)} unique entries (one per edge; the last is open-ended).")
                col["bands"] = {"name": bname.strip(), "edges": [float(e) for e in edges], "labels": [str(x) for x in labels]}
        if ctype == "date":
            fmts = rc.get("date_formats")
            if not isinstance(fmts, list) or not fmts or not all(isinstance(f, str) and "%" in f for f in fmts):
                raise ConfigError(f"{where}: date_formats is required, e.g. ['%d/%m/%Y']. It removes any day/month ambiguity - the tool never guesses.")
            col["date_formats"] = fmts
            if rc.get("min_date") is not None:
                col["min_date"] = _as_date(rc["min_date"], f"{where}.min_date")
            if rc.get("max_date") is not None:
                col["max_date"] = _as_date(rc["max_date"], f"{where}.max_date")
            if col["min_date"] and col["max_date"] and col["min_date"] > col["max_date"]:
                raise ConfigError(f"{where}: min_date is after max_date.")
        if ctype == "multiselect":
            opts = rc.get("options")
            for o in (opts if isinstance(opts, list) else []):
                _no_yaml_surprise(o, f"{where}.options")
            if not isinstance(opts, list) or not opts or len({str(o).casefold() for o in opts}) != len(opts):
                raise ConfigError(f"{where}: 'options' must list every possible choice once (no duplicates).")
            col["options"] = [str(_no_yaml_surprise(o, f"{where}.options")) for o in opts]
            sep = str(rc.get("separator", " "))
            if len(sep) != 1:
                raise ConfigError(f"{where}: separator must be a single character (Kobo uses a space).")
            col["separator"] = sep
            if col["unique"]:
                raise ConfigError(f"{where}: 'unique' cannot be used on a multiselect column.")
        columns.append(col)
    cfg["columns"] = columns
    col_by_name = {c["name"]: c for c in columns}

    # ---- derived columns -----------------------------------------------
    derived = []

    def add_derived(spec):
        if spec["name"].casefold() in seen:
            raise ConfigError(f"derived column name '{spec['name']}' clashes with another column name.")
        seen.add(spec["name"].casefold())
        derived.append(spec)

    for c in columns:
        if c["type"] == "date":
            for kind, dn in zip(("year", "quarter", "month"), derived_column_names(c["name"])):
                add_derived({"name": dn, "type": "category", "kind": kind, "source": c["name"], "decimals": 0})
        if c["type"] == "number" and c["bands"]:
            add_derived({"name": c["bands"]["name"], "type": "category", "kind": "band", "source": c["name"],
                         "edges": c["bands"]["edges"], "labels": c["bands"]["labels"], "decimals": 0})
        if c["type"] == "multiselect":
            for opt in c["options"]:
                add_derived({"name": f"{c['name']}: {opt}", "type": "number", "kind": "option", "source": c["name"],
                             "option": opt, "decimals": 0, "integer": True})
    cfg["derived"] = derived
    spec_by_name = dict(col_by_name)
    spec_by_name.update({d["name"]: d for d in derived})
    cfg["spec_by_name"] = spec_by_name
    groupable = {c["name"] for c in columns if c["type"] == "category"} | {d["name"] for d in derived if d["type"] == "category"}

    # ---- pivots --------------------------------------------------------
    def check_usable(field, where, role):
        sp = spec_by_name.get(field)
        if sp is None:
            raise ConfigError(f"{where}: '{field}' is not a configured column. Available: {sorted(spec_by_name)}")
        if sp.get("pii") == "drop":
            raise ConfigError(f"{where}: '{field}' is set to pii: drop, so it does not exist in the analysis outputs.")
        return sp

    raw_pivots = raw.get("pivots") or []
    if not isinstance(raw_pivots, list):
        raise ConfigError("pivots: must be a list.")
    pivots, pnames = [], set()
    for i, rp in enumerate(raw_pivots, start=1):
        where = f"pivots[{i}]"
        if not isinstance(rp, dict):
            raise ConfigError(f"{where}: must be a mapping.")
        _check_keys(rp, PIVOT_KEYS, where)
        name = str(rp.get("name") or "").strip()
        if not name:
            raise ConfigError(f"{where}: 'name' is required.")
        where = f"pivot '{name}'"
        if name.casefold() in pnames:
            raise ConfigError(f"{where}: duplicate pivot name.")
        pnames.add(name.casefold())
        agg = rp.get("agg", "sum")
        if agg not in AGGS:
            raise ConfigError(f"{where}: agg must be one of {sorted(AGGS)}.")
        rows = rp.get("rows")
        if not isinstance(rows, str):
            raise ConfigError(f"{where}: 'rows' must be exactly one column name (text).")
        columns_field = rp.get("columns")
        if columns_field is not None and not isinstance(columns_field, str):
            raise ConfigError(f"{where}: 'columns' must be one column name or omitted.")
        values = rp.get("values")
        for label, field in (("rows", rows), ("columns", columns_field)):
            if field is not None and field not in groupable:
                raise ConfigError(f"{where}: '{field}' cannot be used for '{label}'. Use a column of type 'category', "
                                  f"a band, or a date-derived column. Available: {sorted(groupable)}")
        if columns_field is not None and columns_field == rows:
            raise ConfigError(f"{where}: rows and columns must be different fields.")
        if agg == "count_rows":
            if values is not None:
                raise ConfigError(f"{where}: agg 'count_rows' counts records and takes no 'values'.")
        else:
            if not isinstance(values, str):
                raise ConfigError(f"{where}: 'values' must name a column (got {values!r}).")
            sp = check_usable(values, where, "values")
            if agg in ("sum", "mean", "min", "max", "median") and sp["type"] != "number":
                raise ConfigError(f"{where}: agg '{agg}' needs a number column; '{values}' is {sp['type']}.")
            if sp["type"] == "multiselect":
                raise ConfigError(f"{where}: use one of the 0/1 option columns, e.g. '{values}: <option>'.")
            if sp.get("pii") and agg != "count_unique":
                raise ConfigError(f"{where}: personal-data column '{values}' can only be used with agg 'count_unique'.")
            if agg == "count_unique" and sp["type"] == "date":
                raise ConfigError(f"{where}: count_unique does not apply to date columns.")
        chart = rp.get("chart", "column")
        if chart not in CHARTS:
            raise ConfigError(f"{where}: chart must be one of {sorted(CHARTS)}.")
        if chart == "pie" and columns_field is not None:
            raise ConfigError(f"{where}: a pie chart needs a single value series - remove 'columns'.")
        if chart == "pie" and agg not in ("sum", "count", "count_rows", "count_unique"):
            raise ConfigError(f"{where}: a pie chart only makes sense for sum/count aggregations.")
        sort = rp.get("sort", "label")
        if sort not in SORTS:
            raise ConfigError(f"{where}: sort must be one of {sorted(SORTS)}.")
        percent_of = rp.get("percent_of")
        if percent_of is not None:
            if percent_of not in ("row", "column", "total"):
                raise ConfigError(f"{where}: percent_of must be row, column or total.")
            if agg not in ADDITIVE_AGGS:
                raise ConfigError(f"{where}: percent_of only works with sum, count or count_rows.")
            if percent_of in ("row", "column") and columns_field is None:
                raise ConfigError(f"{where}: percent_of: {percent_of} needs a 'columns' field; use 'total' otherwise.")
        pivots.append({"name": name, "rows": rows, "columns": columns_field, "values": values, "agg": agg,
                       "chart": chart, "sort": sort, "percent_of": percent_of})
    cfg["pivots"] = pivots

    # ---- indicators ----------------------------------------------------
    def parse_filters(raw_list, where):
        if raw_list is None:
            return []
        if not isinstance(raw_list, list):
            raise ConfigError(f"{where}: must be a list of conditions like [{{column: Sex, equals: Female}}].")
        out = []
        for k, f in enumerate(raw_list, start=1):
            w = f"{where}[{k}]"
            if not isinstance(f, dict) or "column" not in f:
                raise ConfigError(f"{w}: each condition needs a 'column' and exactly one test (equals, in, gte ...).")
            ops = [o for o in f if o != "column"]
            if len(ops) != 1 or ops[0] not in ALL_OPS:
                raise ConfigError(f"{w}: give exactly one test from {sorted(ALL_OPS)}; got {ops}.")
            op, val = ops[0], f[ops[0]]
            sp = check_usable(f["column"], w, "filter")
            if sp.get("pii"):
                raise ConfigError(f"{w}: personal-data column '{f['column']}' cannot be used in conditions.")
            t = sp["type"]
            if t in ("category", "text"):
                if op not in CAT_OPS:
                    raise ConfigError(f"{w}: '{op}' cannot be used on text/category column '{f['column']}'.")
                vals = val if op in ("in", "not_in") else [val]
                if op in ("in", "not_in") and (not isinstance(val, list) or not val):
                    raise ConfigError(f"{w}: '{op}' needs a non-empty list.")
                vals = [str(_no_yaml_surprise(v, f"{w}.{op}")) for v in vals]
                if sp.get("allowed"):
                    canon = {a.casefold(): a for a in sp["allowed"]}
                    fixed = []
                    for v in vals:
                        if v.casefold() not in canon:
                            raise ConfigError(f"{w}: '{v}' is not one of the allowed values for '{f['column']}': {sp['allowed']}")
                        fixed.append(canon[v.casefold()])
                    vals = fixed
                value = vals if op in ("in", "not_in") else vals[0]
            elif t == "number":
                if op in ("in", "not_in"):
                    if not isinstance(val, list) or not val or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in val):
                        raise ConfigError(f"{w}: '{op}' needs a non-empty list of numbers.")
                    value = [float(v) for v in val]
                else:
                    if isinstance(val, bool) or not isinstance(val, (int, float)):
                        raise ConfigError(f"{w}: '{op}' needs a number, got {val!r}.")
                    value = float(val)
            else:
                raise ConfigError(f"{w}: column '{f['column']}' is {t}; use a category/number column "
                                  f"(for dates use '<date> (Year)' / '(Month)'; for multi-select use the '<name>: <option>' 0/1 columns).")
            out.append({"column": f["column"], "op": op, "value": value})
        return out

    raw_inds = raw.get("indicators") or []
    if not isinstance(raw_inds, list):
        raise ConfigError("indicators: must be a list.")
    indicators, inames = [], set()
    for i, ri in enumerate(raw_inds, start=1):
        where = f"indicators[{i}]"
        if not isinstance(ri, dict):
            raise ConfigError(f"{where}: must be a mapping.")
        _check_keys(ri, INDICATOR_KEYS, where)
        name = str(ri.get("name") or "").strip()
        if not name:
            raise ConfigError(f"{where}: 'name' is required.")
        where = f"indicator '{name}'"
        if name.casefold() in inames:
            raise ConfigError(f"{where}: duplicate indicator name.")
        inames.add(name.casefold())
        itype = ri.get("type")
        if itype not in INDICATOR_TYPES:
            raise ConfigError(f"{where}: type must be one of {sorted(INDICATOR_TYPES)}.")
        wh = parse_filters(ri.get("where"), f"{where}.where")
        among = parse_filters(ri.get("among"), f"{where}.among")
        values, unique_by = ri.get("values"), ri.get("unique_by")
        if itype == "percent":
            if not wh:
                raise ConfigError(f"{where}: a percent indicator needs 'where' (who counts as meeting the outcome).")
            if values is not None:
                raise ConfigError(f"{where}: percent indicators take no 'values' (use 'unique_by' to count people instead of records).")
        else:
            if among:
                raise ConfigError(f"{where}: 'among' is only for percent indicators; put all conditions in 'where'.")
            if unique_by is not None:
                raise ConfigError(f"{where}: 'unique_by' is only for percent indicators (use type count_unique otherwise).")
        if itype in ("sum", "mean"):
            sp = check_usable(values, where, "values") if isinstance(values, str) else None
            if sp is None or sp["type"] != "number":
                raise ConfigError(f"{where}: '{itype}' needs 'values' naming a number column.")
        elif itype == "count_unique":
            if not isinstance(values, str):
                raise ConfigError(f"{where}: count_unique needs 'values' naming the ID column to count distinct values of.")
            sp = check_usable(values, where, "values")
            if sp["type"] in ("date", "multiselect"):
                raise ConfigError(f"{where}: cannot count distinct values of a {sp['type']} column.")
        elif itype in ("count",) and values is not None:
            raise ConfigError(f"{where}: 'count' counts records and takes no 'values' (use count_unique to count people).")
        if unique_by is not None:
            sp = check_usable(unique_by, where, "unique_by") if isinstance(unique_by, str) else None
            if sp is None or sp["type"] in ("date", "multiselect"):
                raise ConfigError(f"{where}: unique_by must name an ID column (text/category/number).")
        dis = ri.get("disaggregate_by") or []
        if not isinstance(dis, list) or not all(isinstance(d, str) for d in dis):
            raise ConfigError(f"{where}: disaggregate_by must be a list of category column names.")
        for d in dis:
            if d not in groupable:
                raise ConfigError(f"{where}: cannot disaggregate by '{d}'. Use a category column, a band or a date-derived column: {sorted(groupable)}")
        indicators.append({"name": name, "description": str(ri.get("description") or "").strip(), "type": itype,
                           "where": wh, "among": among, "values": values, "unique_by": unique_by,
                           "disaggregate_by": dis})
    cfg["indicators"] = indicators
    return cfg
