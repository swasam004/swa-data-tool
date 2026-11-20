"""Smart analysis: upload ANY Excel/CSV/JSON, no setup. Finds the table, cleans it, analyses it, answers questions."""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import math
import tempfile
from pathlib import Path

import pandas as pd

from .config import normalize_config
from .pipeline import RunResult, STATUS_INPUT, _fail, _make_out_dir, process_file
from .smart_analyze import analyze
from .smart_clean import Dataset, build_dataset
from .smart_load import SmartError, scan_file
from .smart_qa import Answer, QACol, QAContext, ask, context_from_dataset, row_noun
from .smart_report import build_extras

MAX_QUESTIONS = 30
MAX_SMART_ROWS = 100_000


# ------------------------------------------------------------------------------------ config
def _nunique(ds: Dataset, name: str) -> int:
    return int(ds.df[name].nunique(dropna=True))


def auto_pivots(ds: Dataset) -> list:
    pivots, used = [], set()

    def add(p):
        base, k = p["name"], 2
        while p["name"].casefold() in used:
            p["name"] = f"{base} {k}"
            k += 1
        used.add(p["name"].casefold())
        pivots.append(p)

    cats = [c for c in ds.cols if c.role in ("category", "boolean") and not c.pii and 2 <= _nunique(ds, c.name) <= 25
            and ds.df[c.name].value_counts(normalize=True).iloc[0] < 0.98]
    cats.sort(key=lambda c: _nunique(ds, c.name))
    meas = [c for c in ds.cols if c.role == "measure" and not c.pii]
    key = ("price", "cost", "revenue", "sales", "amount", "income", "salary", "value", "total", "fee")
    meas.sort(key=lambda c: (0 if any(k in c.name.lower() for k in key) else 1))      # money-like columns first
    for c in cats[:5]:
        add({"name": f"Count by {c.name}", "rows": c.name, "agg": "count_rows", "chart": "column" if _nunique(ds, c.name) <= 8 else "bar", "sort": "value_desc"})
    for m in meas[:2]:
        for c in cats[:3]:
            if len(ds.df) / max(_nunique(ds, c.name), 1) >= 2:
                add({"name": f"Avg {m.name} by {c.name}", "rows": c.name, "values": m.name, "agg": "mean", "chart": "bar", "sort": "value_desc"})
    for y in [c for c in ds.cols if c.role == "year" and not c.pii][:1]:
        add({"name": f"Count by {y.name}", "rows": y.name, "agg": "count_rows", "chart": "column", "sort": "label"})
        for m in meas[:1]:
            add({"name": f"Avg {m.name} by {y.name}", "rows": y.name, "values": m.name, "agg": "mean", "chart": "line", "sort": "label"})
    for d in [c for c in ds.cols if c.role == "date" and not c.pii][:1]:
        add({"name": f"Count by {d.name} month", "rows": f"{d.name} (Month)", "agg": "count_rows", "chart": "line", "sort": "label"})
    small = [c for c in cats if _nunique(ds, c.name) <= 8][:2]
    if len(small) == 2:
        add({"name": f"{small[0].name} x {small[1].name}", "rows": small[0].name, "columns": small[1].name, "agg": "count_rows", "percent_of": "row", "chart": "column_stacked"})
    return pivots[:14]


def build_config(ds: Dataset, title: str, outputs: Path, salt: str) -> dict:
    cols = []
    for c in ds.cols:
        d = {"name": c.name}
        if c.pii:
            d.update(type="text", pii=c.pii)
        elif c.role in ("category", "boolean", "year"):
            d.update(type="category")
        elif c.role == "measure":
            d.update(type="number", decimals=c.decimals, integer=bool(c.integer))
        elif c.role == "date":
            d.update(type="date", date_formats=["%Y-%m-%d"])
        else:
            d.update(type="text")
        cols.append(d)
    raw = {"dataset_name": title, "folders": {"inbox": "inbox", "outputs": str(outputs)}, "privacy_salt": salt, "columns": cols,
           "pivots": auto_pivots(ds), "indicators": [], "max_quarantine_pct": 10, "branding": True}
    cfg = normalize_config(raw, base_dir=outputs.parent)
    cfg["_config_path"] = "smart-analysis (automatic)"
    cfg["_config_sha256"] = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    return cfg


def write_csv(ds: Dataset, path: Path) -> None:
    roles = {c.name: c.role for c in ds.cols}
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([c.name for c in ds.cols])
        for row in ds.df.itertuples(index=False):
            out = []
            for name, v in zip(ds.df.columns, row):
                if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
                    out.append("")
                elif roles[name] == "year":
                    out.append(str(int(v)))
                elif roles[name] == "measure":
                    t = repr(float(v))
                    out.append(t[:-2] if t.endswith(".0") else t)
                elif roles[name] == "date":
                    out.append(pd.Timestamp(v).strftime("%Y-%m-%d"))
                else:
                    out.append(str(v))
            w.writerow(out)


# ------------------------------------------------------------------------------------ persistence for later questions
def _safe(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_safe(v) for v in o]
    return o


def save_state(out_dir: Path, ds: Dataset, ctx: QAContext, answers: list, insights: list, title: str) -> None:
    state = {"version": 1, "title": title, "noun": ctx.noun, "source": ds.source,
             "columns": [{"name": c.name, "role": c.role, "prefix": c.prefix, "decimals": c.decimals, "pii": c.pii, "aliases": c.aliases, "source": c.source} for c in ds.cols],
             "answers": [_safe(a.to_dict()) for a in answers], "insights": insights, "notes": ds.notes,
             "log": ds.log, "lookups": ds.lookups}
    (out_dir / "smart.json").write_text(json.dumps(_safe(state), indent=2, default=str), encoding="utf-8")


def load_state(run_dir: Path) -> dict | None:
    p = Path(run_dir) / "smart.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def load_qa_context(run_dir: Path) -> QAContext | None:
    state = load_state(run_dir)
    csv_path = Path(run_dir) / "clean_data.csv"
    if not state or not csv_path.is_file():
        return None
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    cols = {}
    for c in state["columns"]:
        if c["name"] not in df.columns:
            continue
        role = c["role"]
        s = df[c["name"]]
        if role in ("measure", "year"):
            df[c["name"]] = pd.to_numeric(s.replace("", None), errors="coerce")
        elif role == "date":
            df[c["name"]] = pd.to_datetime(s.replace("", None), errors="coerce")
        else:
            df[c["name"]] = s.map(lambda v: v if v != "" else None).astype(object)
        cols[c["name"]] = QACol(c["name"], role, c.get("prefix", ""), c.get("decimals", 0), c.get("pii"), c.get("aliases", []), c.get("source", ""))
    return QAContext(df[list(cols)].reset_index(drop=True), cols, state.get("noun", "rows"))


# ------------------------------------------------------------------------------------ main entry
def run_smart(path, outputs: Path, title: str, questions: list | None = None, display_name: str | None = None,
              salt: str = "", use_ai: bool = True) -> RunResult:
    path = Path(path)
    outputs = Path(outputs)
    outputs.mkdir(parents=True, exist_ok=True)
    now = _dt.datetime.now().astimezone()
    try:
        scan = scan_file(path)
        ds = build_dataset(scan)
        if len(ds.df) < 2:
            raise SmartError("The table has fewer than 2 rows of data.")
        if len(ds.df) > MAX_SMART_ROWS:
            raise SmartError(f"This table has {len(ds.df):,} rows. Smart analysis supports up to {MAX_SMART_ROWS:,} rows because every figure is calculated twice. "
                             "Split the file, or use a structured analysis profile for very large files.")
        A = analyze(ds)
        ctx = context_from_dataset(ds)
        asked, seen = [], set()
        for q in list(scan.questions) + [x for x in (questions or [])]:
            key = " ".join(str(q).lower().split())
            if key and key not in seen and len(asked) < MAX_QUESTIONS:
                seen.add(key)
                asked.append(str(q).strip()[:300])
        answers = [ask(q, ctx, use_ai=use_ai) for q in asked]
        extras = build_extras(ds, A, answers, ctx, scan)
        cfg = build_config(ds, title, outputs, salt)
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / f"{Path(display_name or path.name).stem or 'data'}.csv"
            write_csv(ds, csv_path)
            res = process_file(csv_path, cfg, extras=extras, display_name=display_name or path.name)
        if res.out_dir.exists() and (res.out_dir / "clean_data.csv").exists():
            save_state(res.out_dir, ds, ctx, answers, A.insights, title)
        return res
    except SmartError as exc:
        cfg = {"outputs": str(outputs), "dataset_name": title, "_config_path": "smart-analysis", "_config_sha256": ""}
        out_dir = _make_out_dir(cfg, path, now)
        return _fail(out_dir, {"dataset_name": title, "source_file": display_name or path.name, "run_time": now.isoformat(timespec="seconds")}, str(exc), STATUS_INPUT)
