"""Small shared helpers."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

MISSING_LABEL = "(Missing)"

_NATURAL_SPLIT = re.compile(r"(\d+)")
_NUMERIC_LOOKING = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def natural_key(label: str):
    """Sort key so that 'Item 2' < 'Item 10' and '(Missing)' always sorts last."""
    parts = _NATURAL_SPLIT.split(label)
    key = [int(p) if i % 2 else p.casefold() for i, p in enumerate(parts)]
    return (label == MISSING_LABEL, key)


def csv_safe(value) -> str:
    """Neutralise spreadsheet-formula injection in text written to CSV.

    A text cell starting with = + - @ would be executed as a formula when the
    CSV is opened in Excel. Real numbers (e.g. -5) are left alone.
    """
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in "=+-@" and not _NUMERIC_LOOKING.match(s):
        return "'" + s
    return s


def safe_filename(name: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (cleaned or "file")[:limit]
