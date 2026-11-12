"""Reading input files into a table of *text* cells.

Everything is read as text on purpose. Letting a library guess types is how
leading zeros disappear, dates silently flip day/month, and bad values turn
into NaN without a trace. Typing happens later, explicitly, in cleaning.py.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

SOURCE_ROW = "Source Row"


class InputError(Exception):
    """The input file cannot be processed. The message explains why."""


@dataclass
class RawTable:
    df: pd.DataFrame                      # text cells + 'Source Row'
    malformed: list = field(default_factory=list)   # [(source_row, [fields], reason)]
    n_blank_lines: int = 0                # fully empty lines (CSV only), counted as blank rows
    blank_source_rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    kind: str = ""


def _norm_header(h) -> str:
    return " ".join(str(h).replace("\u00a0", " ").split())


def read_raw(path: Path, cfg: dict) -> RawTable:
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt", ".tsv"):
        return _read_csv(path, cfg)
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return _read_excel(path, cfg)
    if suffix == ".json":
        return _read_json(path)
    raise InputError(f"Unsupported file type '{suffix}'. Supported: .csv, .xlsx, .xls, .json")


# ----------------------------------------------------------------------------- CSV
def _read_csv(path: Path, cfg: dict) -> RawTable:
    rd = cfg["read"]
    delim = "\t" if path.suffix.lower() == ".tsv" else rd["delimiter"]
    encodings = [rd["encoding"]] + (["cp1252"] if rd["encoding"].lower().replace("-", "") not in ("cp1252", "windows1252") else [])
    last_exc = None
    for i, enc in enumerate(encodings):
        try:
            table = _read_csv_with(path, delim, enc, rd["header_row"])
            if i > 0:
                table.warnings.append(
                    f"File is not valid {encodings[0]}; it was read as {enc}. "
                    "Check that accented/special characters look right."
                )
            return table
        except UnicodeDecodeError as exc:
            last_exc = exc
    raise InputError(f"Cannot decode the file text with {encodings}: {last_exc}")


def _read_csv_with(path: Path, delim: str, enc: str, header_row: int) -> RawTable:
    header = None
    rows, malformed, blank_rows = [], [], []
    with open(path, "r", encoding=enc, newline="") as fh:
        reader = csv.reader(fh, delimiter=delim, strict=False)
        prev_line = 0
        try:
            for fields in reader:
                start_line = prev_line + 1
                prev_line = reader.line_num
                if header is None:
                    if start_line == header_row + 1:
                        header = [_norm_header(h) for h in fields]
                    continue
                if not fields or all(not f.strip() for f in fields):
                    blank_rows.append(start_line)
                    continue
                if len(fields) != len(header):
                    malformed.append((start_line, fields,
                                      f"row has {len(fields)} fields but the header has {len(header)}"))
                    continue
                rows.append([start_line] + fields)
        except csv.Error as exc:
            raise InputError(f"CSV structure error near line {reader.line_num}: {exc}") from exc
    if header is None:
        raise InputError("The file has no header row (is it empty, or is read.header_row too large?).")
    df = pd.DataFrame(rows, columns=[SOURCE_ROW] + header, dtype=object)
    df[SOURCE_ROW] = df[SOURCE_ROW].astype(int)
    return RawTable(df=df, malformed=malformed, blank_source_rows=blank_rows,
                    n_blank_lines=len(blank_rows), kind="csv")


# ---------------------------------------------------------------------------- Excel
def _read_excel(path: Path, cfg: dict) -> RawTable:
    rd = cfg["read"]
    sheet = rd["excel_sheet"]
    try:
        xl = pd.ExcelFile(path)
    except Exception as exc:  # corrupt / password protected / wrong format
        raise InputError(f"Cannot open the Excel file: {exc}") from exc
    try:
        names = xl.sheet_names
        if isinstance(sheet, int):
            if not 0 <= sheet < len(names):
                raise InputError(f"excel_sheet {sheet} does not exist; the workbook has {len(names)} sheet(s): {names}")
        elif sheet not in names:
            raise InputError(f"Sheet '{sheet}' not found. Sheets in this workbook: {names}")
        df = xl.parse(sheet_name=sheet, header=rd["header_row"], dtype=str,
                      keep_default_na=False, na_filter=False)
    finally:
        xl.close()
    if df.shape[1] == 0:
        raise InputError("The sheet has no columns.")
    df.columns = [_norm_header(c) for c in df.columns]
    keep = [c for c in df.columns if not (c.startswith("Unnamed:") and (df[c].astype(str).str.strip() == "").all())]
    df = df[keep].astype(object)
    df.insert(0, SOURCE_ROW, [i + rd["header_row"] + 2 for i in range(len(df))])
    return RawTable(df=df, kind="excel")


# ----------------------------------------------------------------------------- JSON
def _read_json(path: Path) -> RawTable:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InputError(f"Invalid JSON: {exc}") from exc
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        if len(lists) == 1:
            data = lists[0]
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise InputError("JSON must be a list of records (objects), e.g. [{\"a\": 1}, {\"a\": 2}].")
    header: list[str] = []
    for rec in data:
        for k in rec:
            nk = _norm_header(k)
            if nk not in header:
                header.append(nk)
    rows = []
    for i, rec in enumerate(data, start=1):
        norm = {_norm_header(k): v for k, v in rec.items()}
        row = [i]
        for h in header:
            v = norm.get(h)
            if v is None:
                row.append("")
            elif isinstance(v, (dict, list)):
                row.append(json.dumps(v))
            else:
                row.append(str(v))
        rows.append(row)
    df = pd.DataFrame(rows, columns=[SOURCE_ROW] + header, dtype=object)
    return RawTable(df=df, kind="json",
                    warnings=["JSON input: 'Source Row' is the record number (1 = first record)."])


# ------------------------------------------------------------------- header mapping
def map_headers(table: RawTable, cfg: dict) -> tuple[pd.DataFrame, list, list]:
    """Return (df with exactly the configured columns + Source Row, warnings, ignored_columns)."""
    import difflib

    df = table.df
    file_cols = [c for c in df.columns if c != SOURCE_ROW]
    by_key: dict[str, list[str]] = {}
    for c in file_cols:
        by_key.setdefault(c.casefold(), []).append(c)
    dups = {k: v for k, v in by_key.items() if len(v) > 1}
    if dups:
        raise InputError(f"The file has duplicate column headers (case-insensitive): {sorted(dups)}. "
                         "Rename them so each header is unique.")
    warnings = list(table.warnings)
    out = pd.DataFrame({SOURCE_ROW: df[SOURCE_ROW].values})
    used = set()
    missing_required, missing_optional = [], []
    for col in cfg["columns"]:
        wanted = [col["source"]] + ([col["name"]] if col["name"] != col["source"] else []) + col["aliases"]
        found = []
        for w in wanted:
            k = _norm_header(w).casefold()
            if k in by_key and by_key[k][0] not in found:
                found.append(by_key[k][0])
        if len(found) > 1:
            raise InputError(f"Column '{col['name']}' matches more than one header in the file: {found}. "
                             "Remove the ambiguity with the 'source' setting.")
        if found:
            actual = found[0]
            used.add(actual)
            if actual not in (col["name"], col["source"]) and actual.casefold() != _norm_header(col["source"]).casefold():
                warnings.append(f"Column '{actual}' in the file was matched to '{col['name']}' through its aliases.")
            elif actual != col["name"] and actual.casefold() == col["name"].casefold():
                warnings.append(f"Column '{actual}' in the file was matched to configured column '{col['name']}' (differs only in case/spacing).")
            out[col["name"]] = df[actual].values
        else:
            (missing_required if col["required"] else missing_optional).append(col["name"])
    if missing_required:
        hints = []
        for m in missing_required:
            close = difflib.get_close_matches(m, file_cols, n=2, cutoff=0.5)
            hints.append(f"'{m}'" + (f" (did you mean {close}?)" if close else ""))
        raise InputError(
            "Required column(s) not found in the file: " + ", ".join(hints)
            + f". Columns found: {file_cols}"
        )
    for m in missing_optional:
        warnings.append(f"Optional column '{m}' is not in the file; all its values are treated as missing.")
        out[m] = [""] * len(out)
    ignored = [c for c in file_cols if c not in used]
    if ignored:
        warnings.append(f"File columns not listed in the configuration were ignored: {ignored}")
    return out, warnings, ignored
