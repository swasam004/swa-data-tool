"""Protection of personal data (names, phone numbers, ID numbers) in everything the tool WRITES.

Analysis is done on the real values (so unique counts are exact); only the
outputs are protected:
  hash : replaced by a salted SHA-256 code - the same person always gets the
         same code, so records can still be matched/counted, but the value
         cannot be read back.  (Set `privacy_salt` to a long secret phrase.)
  mask : only the last 3 characters are kept (e.g. *******866)
  drop : the column is removed from every output file
Reasons shown for rejected rows never echo values of protected columns.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from .readers import SOURCE_ROW


def hash_value(value: str, column: str, salt: str) -> str:
    if value == "" or value is None:
        return ""
    return hashlib.sha256(f"{salt}|{column}|{value}".encode("utf-8")).hexdigest()[:16]


def mask_value(value: str) -> str:
    if value == "" or value is None:
        return ""
    s = str(value)
    return "*" * len(s) if len(s) < 6 else "*" * (len(s) - 3) + s[-3:]


def _apply(series: pd.Series, mode: str, column: str, salt: str) -> pd.Series:
    if mode == "hash":
        return series.map(lambda v: hash_value("" if v is None else str(v), column, salt))
    return series.map(lambda v: mask_value("" if v is None else str(v)))


def protect_frames(clean: pd.DataFrame, quarantined: pd.DataFrame, cfg: dict):
    clean_out, quar_out = clean.copy(), quarantined.copy()
    protected = []
    for c in cfg["columns"]:
        mode = c.get("pii")
        if not mode:
            continue
        protected.append((c["name"], mode))
        if mode == "drop":
            clean_out = clean_out.drop(columns=[c["name"]], errors="ignore")
            quar_out = quar_out.drop(columns=[c["name"]], errors="ignore")
        else:
            if c["name"] in clean_out.columns:
                clean_out[c["name"]] = _apply(clean_out[c["name"]], mode, c["name"], cfg["privacy_salt"])
            if c["name"] in quar_out.columns and len(quar_out):
                quar_out[c["name"]] = _apply(quar_out[c["name"]], mode, c["name"], cfg["privacy_salt"])
    return clean_out, quar_out, protected
