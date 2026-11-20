"""Smart loading: find the real data table(s) inside any workbook or CSV.

Real files are messy: titles above the table, the table starting in column B, several tables on one sheet, lookup
sheets, 'answer' sheets that repeat the data, total rows. This module reads every visible sheet as a grid of values,
finds table-like blocks, classifies them (table / lookup / notes) and chooses what to analyse - and it records, in plain
words, everything it decided so nothing is hidden.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import datetime as _dt

MAX_ROWS = 500_000
ANSWER_SHEET = re.compile(r"(solution|answer|key|working|scratch|helper|calc)", re.I)
QUESTION_SHEET = re.compile(r"(question|task|exam|quiz|assignment|brief)", re.I)
INTERROGATIVE = re.compile(r"^(what|which|how|who|where|when|why|is|are|do|does|did|can|could|list|find|calculate|show|determine|identify|compare|rank|count|summari[sz]e|give)\b", re.I)
TOTAL_LABEL = re.compile(r"^\s*(grand\s+total|sub-?total|total|totals|overall|sum|average|mean)\s*:?\s*$", re.I)
NUMERIC_TEXT = re.compile(r"^[\s$£€(+-]*\d[\d,.\s]*%?\)?\s*$")


class SmartError(Exception):
    """The file could not be understood. The message says what to do."""


def blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def colname(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


@dataclass
class Block:
    sheet: str
    top: int                    # 0-based grid coordinates
    left: int
    bottom: int
    right: int
    header: list = field(default_factory=list)
    rows: list = field(default_factory=list)          # list of lists (raw python values)
    row_numbers: list = field(default_factory=list)   # 1-based sheet row of each data row
    synthetic_header: bool = False
    kind: str = "table"                               # table | lookup | notes
    dropped: list = field(default_factory=list)       # human messages about rows removed

    @property
    def ref(self) -> str:
        return f"{colname(self.left)}{self.top + 1}:{colname(self.right)}{self.bottom + 1}"

    @property
    def signature(self) -> tuple:
        return tuple(re.sub(r"\s+", " ", str(h)).strip().casefold() for h in self.header)

    def content_key(self) -> tuple:
        return (self.signature, tuple(tuple(_norm_cell(v) for v in r) for r in self.rows))


def _norm_cell(v):
    if isinstance(v, str):
        return re.sub(r"\s+", " ", v).strip()
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


@dataclass
class Scan:
    file_kind: str
    sheets: dict                                       # name -> grid
    blocks: list = field(default_factory=list)
    primary: list = field(default_factory=list)        # blocks that form the analysed table (stacked if several)
    lookups: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    notes: list = field(default_factory=list)          # plain-language decisions
    hidden_sheets: list = field(default_factory=list)


# ------------------------------------------------------------------------------------------ reading
def read_grids(path: Path) -> tuple[str, dict, list]:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        except Exception as exc:
            raise SmartError(f"Cannot open the Excel file: {exc}") from exc
        sheets, hidden = {}, []
        try:
            for ws in wb.worksheets:
                if getattr(ws, "sheet_state", "visible") != "visible":
                    hidden.append(ws.title)
                    continue
                grid = []
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= MAX_ROWS:
                        break
                    grid.append(list(row))
                while grid and all(blank(v) for v in grid[-1]):
                    grid.pop()
                sheets[ws.title] = grid
        finally:
            wb.close()
        return "excel", sheets, hidden
    if suffix == ".xls":
        raise SmartError("Old .xls files are not supported. In Excel choose File > Save As > Excel Workbook (.xlsx) and upload that.")
    if suffix in (".csv", ".tsv", ".txt"):
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sample = text[:20000]
        if suffix == ".tsv":
            delim = "\t"
        else:
            counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
            delim = max(counts, key=counts.get) if max(counts.values()) else ","
        grid = [row for row in csv.reader(io.StringIO(text), delimiter=delim)]
        while grid and all(blank(v) for v in grid[-1]):
            grid.pop()
        return "csv", {path.stem: grid[:MAX_ROWS]}, []
    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise SmartError(f"Invalid JSON: {exc}") from exc
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            data = lists[0] if len(lists) == 1 else data
        if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
            raise SmartError("JSON must be a list of records, e.g. [{\"a\": 1}, {\"a\": 2}].")
        keys = []
        for r in data:
            for k in r:
                if k not in keys:
                    keys.append(k)
        grid = [keys] + [[(json.dumps(r.get(k)) if isinstance(r.get(k), (dict, list)) else r.get(k)) for k in keys] for r in data]
        return "json", {path.stem: grid}, []
    raise SmartError(f"Unsupported file type '{suffix}'. Use .xlsx, .csv or .json.")


# ------------------------------------------------------------------------------------ block finding
def _looks_like_header(cells: list) -> bool:
    ne = [c for c in cells if not blank(c)]
    if len(ne) < 2 or len(ne) < math.ceil(0.6 * len(cells)):
        return False
    if not all(isinstance(c, str) for c in ne):
        return False
    if any(NUMERIC_TEXT.match(c) for c in ne):
        return False
    if any(len(c) > 70 for c in ne):
        return False
    keys = [re.sub(r"\s+", " ", c).strip().casefold() for c in ne]
    return len(set(keys)) == len(keys)


def find_blocks(sheet: str, grid: list) -> list:
    if not grid:
        return []
    ncols = max(len(r) for r in grid)
    grid = [list(r) + [None] * (ncols - len(r)) for r in grid]
    ne = [[c for c, v in enumerate(row) if not blank(v)] for row in grid]
    wide = [len(x) >= 2 for x in ne]
    for r in range(1, len(grid) - 1):                       # sparse rows inside a table stay part of it
        if len(ne[r]) == 1 and wide[r - 1] and wide[r + 1]:
            v = grid[r][ne[r][0]]
            if not (isinstance(v, str) and len(v) > 60):
                wide[r] = True
    groups, start = [], None
    for r, w in enumerate(wide + [False]):
        if w and start is None:
            start = r
        if not w and start is not None:
            groups.append((start, r - 1))
            start = None
    blocks = []
    for top, bottom in groups:
        occupied = sorted({c for r in range(top, bottom + 1) for c in ne[r]})
        spans, cur = [], [occupied[0]]
        for c in occupied[1:]:
            if c == cur[-1] + 1:
                cur.append(c)
            else:
                spans.append(cur)
                cur = [c]
        spans.append(cur)
        for span in spans:
            if len(span) < 2:
                continue
            left, right = span[0], span[-1]
            rows = [r for r in range(top, bottom + 1) if any(not blank(grid[r][c]) for c in range(left, right + 1))]
            if len(rows) < 2:
                continue
            blocks.append(_build_block(sheet, grid, rows, left, right))
    return [b for b in blocks if b is not None]


def _build_block(sheet, grid, rows, left, right):
    first = rows[0]
    cells = [grid[first][c] for c in range(left, right + 1)]
    synthetic = not _looks_like_header(cells)
    if synthetic:
        header = [f"Column {colname(c)}" for c in range(left, right + 1)]
        data_rows = rows
    else:
        header, seen = [], {}
        for i, c in enumerate(cells):
            h = re.sub(r"\s+", " ", str(c)).strip() if not blank(c) else f"Column {colname(left + i)}"
            k = h.casefold()
            seen[k] = seen.get(k, 0) + 1
            header.append(h if seen[k] == 1 else f"{h} ({seen[k]})")
        data_rows = rows[1:]
    b = Block(sheet=sheet, top=first, left=left, bottom=rows[-1], right=right, header=header, synthetic_header=synthetic)
    hkeys = [h.casefold() for h in header]
    for r in data_rows:
        vals = [grid[r][c] for c in range(left, right + 1)]
        if all(blank(v) for v in vals):
            continue
        if not synthetic and [re.sub(r"\s+", " ", str(v)).strip().casefold() for v in vals if not blank(v)] == [k for k, v in zip(hkeys, vals) if not blank(v)]:
            b.dropped.append(f"Row {r + 1}: a repeated header line was removed.")
            continue
        labels = [v for v in vals if isinstance(v, str)]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if labels and any(TOTAL_LABEL.match(s) for s in labels) and nums:
            b.dropped.append(f"Row {r + 1}: a '{[s for s in labels if TOTAL_LABEL.match(s)][0].strip()}' summary row was removed so it is not counted as data.")
            continue
        b.rows.append(vals)
        b.row_numbers.append(r + 1)
    width = len(header)
    if len(b.rows) < 2 or width < 2:
        return None
    fill = sum(1 for row in b.rows for v in row if not blank(v)) / (len(b.rows) * width)
    if fill < 0.3:
        return None
    # lookup: two text columns, unique keys, small
    if width == 2 and len(b.rows) <= 1000 and fill > 0.95:
        k0 = [re.sub(r"\s+", " ", str(r[0])).strip().casefold() for r in b.rows]
        if len(set(k0)) == len(k0) and all(isinstance(r[1], str) and not NUMERIC_TEXT.match(r[1]) for r in b.rows) \
                and all(isinstance(r[0], str) for r in b.rows):
            b.kind = "lookup"
    return b


# ------------------------------------------------------------------------------------------ questions
def find_questions(sheets: dict) -> list:
    found, seen = [], set()
    for name, grid in sheets.items():
        in_q_sheet = bool(QUESTION_SHEET.search(name))
        for r, row in enumerate(grid):
            for c, v in enumerate(row):
                if not isinstance(v, str):
                    continue
                t = re.sub(r"\s+", " ", v).strip()
                if len(t) < 12 or len(t) > 300:
                    continue
                numbered = c > 0 and isinstance(row[c - 1], (int, float)) and not isinstance(row[c - 1], bool) and float(row[c - 1]).is_integer()
                looks_q = t.endswith("?") or bool(INTERROGATIVE.match(t))
                if not looks_q:
                    continue
                if not (in_q_sheet or (numbered and t.endswith("?"))):
                    continue
                key = re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
                if key in seen:
                    continue
                seen.add(key)
                found.append(t)
    return found


# ------------------------------------------------------------------------------------------ choosing
def scan_file(path: Path) -> Scan:
    kind, sheets, hidden = read_grids(path)
    scan = Scan(file_kind=kind, sheets=sheets, hidden_sheets=hidden)
    if hidden:
        scan.notes.append(f"Hidden sheet(s) were not read: {', '.join(hidden)}.")
    for name, grid in sheets.items():
        scan.blocks.extend(find_blocks(name, grid))
    scan.questions = find_questions(sheets)
    qkeys = {re.sub(r"[^a-z0-9]+", " ", q.lower()).strip() for q in scan.questions}
    for b in scan.blocks:                                   # a numbered list of questions is not data
        cells = [re.sub(r"[^a-z0-9]+", " ", str(v).lower()).strip() for r in b.rows for v in r if isinstance(v, str)]
        cells += [re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip() for h in b.header]
        if any(c in qkeys for c in cells):
            b.kind = "questions"
    if not scan.blocks:
        raise SmartError("I could not find a table in this file. A table needs a header row (column names) and at least 2 rows of data.")
    tables = [b for b in scan.blocks if b.kind == "table"]
    lookups = [b for b in scan.blocks if b.kind == "lookup"]
    if not tables:                                          # a two-text-column file is a table, not a lookup
        tables, lookups = lookups[:1] and [max(lookups, key=lambda b: len(b.rows))], []
    primary_pool = [b for b in tables if not ANSWER_SHEET.search(b.sheet)]
    if not primary_pool:
        primary_pool = tables
        scan.notes.append("Only 'answer/solution' style sheets contained tables, so I used them.")
    best = max(primary_pool, key=lambda b: (len(b.rows) * len(b.header), -list(scan.sheets).index(b.sheet)))
    chosen = [best]
    # same header elsewhere: identical content is a duplicate, different content is stacked
    seen_content = {best.content_key()}
    ignored: dict = {}
    for b in tables:
        if b is best:
            continue
        if b.signature == best.signature:
            if b.content_key() in seen_content:
                ignored.setdefault((b.sheet, "repeats a table that is already used"), []).append(b)
                continue
            seen_content.add(b.content_key())
            chosen.append(b)
            scan.notes.append(f"Added {b.sheet}!{b.ref}: same columns as the main table but different rows, so the rows were stacked.")
    for b in tables:
        if b not in chosen and not any(b.signature == c.signature for c in chosen) and not any(b in v for v in ignored.values()):
            why = ("is on an answer/solution sheet" if ANSWER_SHEET.search(b.sheet) else "is a smaller table than the main one")
            ignored.setdefault((b.sheet, why), []).append(b)
    for (sheet, why), bl in ignored.items():
        refs = ", ".join(x.ref for x in bl[:3]) + (" ..." if len(bl) > 3 else "")
        scan.notes.append(f"Ignored {len(bl)} table(s) on '{sheet}' ({refs}): each {why}.")
    # solution sheets with extra helper columns around the same data are noise: one summary line
    scan.primary = chosen
    scan.lookups = [lk for lk in lookups if not ANSWER_SHEET.search(lk.sheet) or not any(not ANSWER_SHEET.search(x.sheet) for x in lookups)]
    for b in chosen:
        for msg in b.dropped:
            scan.notes.append(f"{b.sheet}: {msg}")
    return scan
