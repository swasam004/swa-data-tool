"""One complete, self-checking run for one input file."""
from __future__ import annotations

import csv
import datetime as _dt
import json
import logging
import platform
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import __version__
from .checks import run_checks
from .cleaning import clean_table
from .html_report import build_html
from .indicators import compute_indicator, independent_indicator
from .pivots import compute_pivot, independent_pivot
from .privacy import protect_frames
from .readers import SOURCE_ROW, InputError, map_headers, read_raw
from .util import csv_safe, safe_filename, sha256_file
from .xlsx_report import write_workbook

log = logging.getLogger("swa")

STATUS_OK = "PASSED"
STATUS_WARN = "PASSED WITH WARNINGS"
STATUS_FAIL = "FAILED"
STATUS_INPUT = "FAILED (input could not be processed)"


@dataclass
class RunResult:
    status: str
    out_dir: Path
    files: list = field(default_factory=list)
    message: str = ""
    summary: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_OK, STATUS_WARN)


def _make_out_dir(cfg: dict, source: Path, now: _dt.datetime) -> Path:
    base = Path(cfg["outputs"]) / f"{now:%Y%m%d_%H%M%S}_{safe_filename(source.stem)}"
    out, k = base, 2
    while out.exists():
        out = Path(f"{base}_{k}")
        k += 1
    out.mkdir(parents=True)
    return out


def _write_csv(path: Path, df: pd.DataFrame, dates: list[str], text_cols: set):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow([csv_safe(c) for c in df.columns])
        for row in df.itertuples(index=False):
            out = []
            for name, v in zip(df.columns, row):
                if v is None or (isinstance(v, float) and v != v) or v is pd.NaT:
                    out.append("")
                elif name in dates:
                    out.append(v.strftime("%Y-%m-%d"))
                elif isinstance(v, float):
                    t = repr(v)                  # full precision, no rounding
                    out.append(t[:-2] if t.endswith(".0") else t)
                elif name in text_cols:
                    out.append(csv_safe(v))
                else:
                    out.append(v)
            w.writerow(out)


def process_file(path, cfg: dict, extras: dict | None = None, display_name: str | None = None) -> RunResult:
    """extras (smart analysis): {"front": [sheet specs], "back": [sheet specs], "checks": [Check], "warnings": [str], "audit": dict}"""
    path = Path(path).resolve()
    now = _dt.datetime.now().astimezone()
    out_dir = _make_out_dir(cfg, path, now)
    fh = logging.FileHandler(out_dir / "run.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    audit = {"dataset_name": cfg["dataset_name"], "tool": f"SWA Data Analysis Tool {__version__}", "python": platform.python_version(), "pandas": pd.__version__,
             "source_file": display_name or path.name, "run_time": now.isoformat(timespec="seconds"),
             "config_file": Path(cfg["_config_path"]).name, "config_sha256": cfg["_config_sha256"]}
    try:
        log.info("Processing %s", path)
        audit["source_sha256"] = sha256_file(path)
        audit["source_bytes"] = path.stat().st_size
        table = read_raw(path, cfg)
        raw, header_warnings, ignored = map_headers(table, cfg)
        cr = clean_table(raw, table, cfg, header_warnings)
        if cr.n_read == 0:
            raise InputError("The file contains no data rows.")
        log.info("Rows: read=%s clean=%s quarantined=%s blank=%s", cr.n_read, cr.n_clean, cr.n_quarantined, cr.n_blank)

        pivots_pd = [compute_pivot(cr.clean, s, cfg) for s in cfg["pivots"]]
        pivots_ind = [independent_pivot(cr.clean, s, cfg) for s in cfg["pivots"]]
        inds_pd = [compute_indicator(cr.clean, s) for s in cfg["indicators"]]
        inds_ind = [independent_indicator(cr.clean, s) for s in cfg["indicators"]]
        for ir in inds_pd:
            cr.warnings.extend(ir.warnings)
        clean_out, quar_out, protected = protect_frames(cr.clean, cr.quarantined, cfg)
        if any(m == "hash" for _, m in protected) and not cfg["privacy_salt"]:
            cr.warnings.append("Personal data is hashed WITHOUT a secret 'privacy_salt' in the configuration; "
                               "anyone who can guess values (e.g. phone numbers) could recompute the codes. Set a long secret phrase.")
        checks = run_checks(cr, cfg, pivots_pd, pivots_ind, inds_pd, inds_ind)
        if extras:
            checks = list(checks) + list(extras.get("checks", []))
            cr.warnings.extend(extras.get("warnings", []))
        failed = [c for c in checks if not c.passed]

        # Excel live-formula checks are added inside the workbook; python-side status first
        status = STATUS_FAIL if failed else (STATUS_WARN if cr.warnings else STATUS_OK)
        ctx = {"cfg": cfg, "cr": cr, "pivots": pivots_pd, "checks": checks, "status": status,
               "warnings": cr.warnings, "source_name": display_name or path.name, "extras": extras or {},
               "indicators": inds_pd, "clean_out": clean_out, "quar_out": quar_out, "protected": protected, "sha256": audit["source_sha256"],
               "run_time": now.strftime("%Y-%m-%d %H:%M:%S %z"), "config_name": audit["config_file"]}

        files = []
        xinfo = write_workbook(out_dir / "report.xlsx", ctx)
        files.append("report.xlsx")
        (out_dir / "report.html").write_text(build_html(ctx), encoding="utf-8")
        files.append("report.html")
        spec = cfg["spec_by_name"]
        date_cols = [n for n in clean_out.columns if n in spec and spec[n]["type"] == "date"]
        text_cols = {n for n in clean_out.columns if n != SOURCE_ROW and spec.get(n, {}).get("type") not in ("number", "date")}
        _write_csv(out_dir / "clean_data.csv", clean_out, date_cols, text_cols)
        files.append("clean_data.csv")
        _write_csv(out_dir / "quarantined_rows.csv", quar_out, [], set(quar_out.columns))
        files.append("quarantined_rows.csv")

        audit.update({
            "status": status,
            "rows": {"read": cr.n_read, "analysed": cr.n_clean, "quarantined": cr.n_quarantined, "blank": cr.n_blank},
            "warnings": cr.warnings,
            "checks": [{"name": c.name, "expected": c.expected, "actual": c.actual, "passed": c.passed, "detail": c.detail} for c in checks],
            "quarantine_breakdown": [{"column": a, "problem": b, "rows": n} for a, b, n in cr.quarantine_breakdown],
            "excel_live_formulas": xinfo["formulas"],
            "pivot_grand_totals": {p.spec["name"]: p.grand_total for p in pivots_pd},
            "indicator_totals": {i.spec["name"]: {"value": i.sections[0][1][0].value, "n": i.sections[0][1][0].n,
                                                  "d": i.sections[0][1][0].d} for i in inds_pd},
            "protected_columns": [{"column": n, "mode": m} for n, m in protected],
            "completeness": cr.completeness,
            "smart": (extras or {}).get("audit"),
            "outputs": files + ["audit.json", "run.log"],
        })
        (out_dir / "audit.json").write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
        files.append("audit.json")
        for c in failed:
            log.error("CHECK FAILED: %s | expected %s | actual %s | %s", c.name, c.expected, c.actual, c.detail)
        log.info("Finished: %s", status)
        return RunResult(status, out_dir, files + ["run.log"],
                         message="; ".join(c.name for c in failed), summary=audit["rows"])
    except InputError as exc:
        return _fail(out_dir, audit, str(exc), STATUS_INPUT)
    except Exception as exc:  # unexpected: never produce numbers we cannot trust
        log.error("Unexpected error:\n%s", traceback.format_exc())
        return _fail(out_dir, audit, f"Unexpected error: {exc!r} (see run.log)", STATUS_FAIL)
    finally:
        log.removeHandler(fh)
        fh.close()


def _fail(out_dir: Path, audit: dict, message: str, status: str) -> RunResult:
    log.error("%s", message)
    audit.update({"status": status, "error": message})
    (out_dir / "audit.json").write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    (out_dir / "ERROR.txt").write_text(
        f"SWA Data Analysis Tool could not complete this run.\n\n{message}\n\nNo report was produced, so no unverified numbers exist.\n",
        encoding="utf-8")
    return RunResult(status, out_dir, ["ERROR.txt", "audit.json", "run.log"], message=message)
