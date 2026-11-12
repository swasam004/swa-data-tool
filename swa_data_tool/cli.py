from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .pipeline import process_file
from .watcher import scan_once, watch

TEMPLATES = Path(__file__).resolve().parent / "templates"


def _setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="swa_data_tool", description="SWA Data Analysis Tool - automated, self-checking data analysis.")
    ap.add_argument("--version", action="version", version=f"SWA Data Analysis Tool {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="Analyse one file now.")
    p.add_argument("file"); p.add_argument("--config", "-c", default="config.yaml")
    p = sub.add_parser("scan", help="Analyse every file currently in the inbox, then stop.")
    p.add_argument("--config", "-c", default="config.yaml")
    p = sub.add_parser("watch", help="Run in the background; analyse files as they arrive in the inbox.")
    p.add_argument("--config", "-c", default="config.yaml"); p.add_argument("--interval", type=float, default=15.0)
    p = sub.add_parser("serve", help="Start the web interface (upload a file in your browser, get the report).")
    p.add_argument("--config", "-c", default="config.yaml"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8080)
    p = sub.add_parser("check-config", help="Validate a configuration file without running anything.")
    p.add_argument("--config", "-c", default="config.yaml")
    p = sub.add_parser("init", help="Create a ready-to-use project folder with an example configuration.")
    p.add_argument("folder"); p.add_argument("--template", choices=["ngo", "sales"], default="ngo")
    args = ap.parse_args(argv)
    _setup_logging()

    if args.cmd == "init":
        dest = Path(args.folder)
        (dest / "inbox").mkdir(parents=True, exist_ok=True)
        (dest / "outputs").mkdir(exist_ok=True)
        template = {"ngo": ("config_ngo.yaml", "ngo_beneficiary_register.csv"),
                    "sales": ("config_sales.yaml", "sales_sample.csv")}[args.template]
        if (dest / "config.yaml").exists():
            print(f"{dest / 'config.yaml'} already exists - left unchanged.")
        else:
            shutil.copy(TEMPLATES / template[0], dest / "config.yaml")
        sample = TEMPLATES / template[1]
        if sample.exists() and not (dest / template[1]).exists():
            shutil.copy(sample, dest / template[1])
        print(f"Project created in {dest.resolve()}\nNext: edit config.yaml, then try:\n"
              f"  cd {dest}\n  swa-tool run {template[1]} --config config.yaml")
        return 0
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "check-config":
        print(f"Configuration OK: {len(cfg['columns'])} column(s), {len(cfg['pivots'])} pivot(s).")
        return 0
    if args.cmd == "run":
        Path(cfg["outputs"]).mkdir(parents=True, exist_ok=True)
        res = process_file(args.file, cfg)
        print(f"\nStatus: {res.status}\nOutput: {res.out_dir}")
        if res.message:
            print(f"Note:   {res.message}")
        return 0 if res.ok else 1
    if args.cmd == "serve":
        from .web import serve
        print(f"Open http://{args.host}:{args.port} in your browser. Press Ctrl+C to stop.")
        serve(args.config, args.host, args.port)
        return 0
    if args.cmd == "scan":
        results = scan_once(cfg)
        return 0 if all(r.ok for _, r in results) else 1
    if args.cmd == "watch":
        watch(cfg, args.interval)
        return 0
    return 0
