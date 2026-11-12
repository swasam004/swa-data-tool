"""Background folder watcher: drop a file into the inbox, get a report."""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import time
from pathlib import Path

from .pipeline import STATUS_FAIL, STATUS_INPUT, process_file
from .util import sha256_file

log = logging.getLogger("swa")
STATE_NAME = ".swa_state.json"
TEMP_PREFIXES = ("~$", ".", "_")
TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", ".partial", ".swp")


def _load_state(inbox: Path) -> dict:
    p = inbox / STATE_NAME
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("State file unreadable; starting fresh.")
    return {}


def _save_state(inbox: Path, state: dict) -> None:
    tmp = inbox / (STATE_NAME + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, inbox / STATE_NAME)


def _candidates(inbox: Path, patterns: list) -> list:
    out = []
    for p in sorted(inbox.iterdir()):
        if not p.is_file() or p.name.startswith(TEMP_PREFIXES) or p.name.lower().endswith(TEMP_SUFFIXES):
            continue
        if any(fnmatch.fnmatch(p.name.lower(), pat.lower()) for pat in patterns):
            out.append(p)
    return out


def _move(path: Path, folder: Path) -> Path:
    folder.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = folder / f"{stamp}_{path.name}"
    k = 2
    while dest.exists():
        dest = folder / f"{stamp}_{k}_{path.name}"
        k += 1
    shutil.move(str(path), str(dest))
    return dest


def scan_once(cfg: dict, seen: dict | None = None, require_stable: bool = False) -> list:
    """Process every ready file once. Returns list of (filename, RunResult)."""
    inbox = Path(cfg["inbox"])
    inbox.mkdir(parents=True, exist_ok=True)
    Path(cfg["outputs"]).mkdir(parents=True, exist_ok=True)
    state = _load_state(inbox)
    seen = seen if seen is not None else {}
    results = []
    for f in _candidates(inbox, cfg["file_patterns"]):
        st = f.stat()
        sig = (st.st_size, st.st_mtime)
        if require_stable:
            # a file is ready only when it did not change between two scans and is not brand new
            if seen.get(f.name) != sig or time.time() - st.st_mtime < 3:
                seen[f.name] = sig
                continue
        try:
            digest = sha256_file(f)
            if digest in state:
                log.info("%s was already processed on %s - moved to _duplicates (not analysed again).", f.name, state[digest]["when"])
                _move(f, inbox / "_duplicates")
                continue
            res = process_file(f, cfg)
            state[digest] = {"file": f.name, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "status": res.status, "output": str(res.out_dir)}
            _save_state(inbox, state)
            _move(f, inbox / ("_failed" if res.status in (STATUS_FAIL, STATUS_INPUT) else "_processed"))
            seen.pop(f.name, None)
            results.append((f.name, res))
            log.info("%s -> %s  (%s)", f.name, res.status, res.out_dir)
        except Exception:
            log.exception("Could not process %s; it was left in the inbox.", f.name)
    return results


def watch(cfg: dict, interval: float = 15.0) -> None:
    log.info("Watching %s every %.0fs. Press Ctrl+C to stop.", cfg["inbox"], interval)
    seen: dict = {}
    try:
        while True:
            scan_once(cfg, seen, require_stable=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Stopped.")
