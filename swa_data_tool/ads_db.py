"""Optional free-database storage for ads (Neon, Supabase, or any PostgreSQL).

Why: on Render's free plan the disk is wiped on every restart. With SWA_DATABASE_URL (or DATABASE_URL) set, every ad
change is also saved - ads.json plus the ad images, as one backup .zip - in a single database row, and that row is
loaded again when the site starts. The local ads.json / ads/ files stay as a fast cache, so nothing else changes.
"""
from __future__ import annotations

import os
import threading
import time

from .ads import Ads, build_backup, restore_backup

TABLE = "swa_ads_store"


class PgStore:
    """One row (id=1) holding the whole ads backup zip."""

    def __init__(self, url: str):
        self.url = url

    def _connect(self):
        import psycopg                                   # imported late: only needed when a database is configured
        return psycopg.connect(self.url, autocommit=True, connect_timeout=10, prepare_threshold=None)

    def get(self) -> bytes | None:
        with self._connect() as con:
            con.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (id INT PRIMARY KEY, data BYTEA NOT NULL, updated TIMESTAMPTZ DEFAULT now())")
            row = con.execute(f"SELECT data FROM {TABLE} WHERE id = 1").fetchone()
            return bytes(row[0]) if row else None

    def put(self, raw: bytes) -> None:
        with self._connect() as con:
            con.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (id INT PRIMARY KEY, data BYTEA NOT NULL, updated TIMESTAMPTZ DEFAULT now())")
            con.execute(f"INSERT INTO {TABLE} (id, data, updated) VALUES (1, %s, now()) "
                        f"ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated = now()", (raw,))


def database_url() -> str:
    url = (os.environ.get("SWA_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgres://"):                    # some providers still hand out the old prefix
        url = "postgresql://" + url[len("postgres://"):]
    return url


def attach(ads: Ads, image_dir, store=None) -> None:
    """Load ads from the database (or seed it from the local files) and keep it updated after every change.

    `store` is only passed by tests. Any failure is shown in the admin checklist (ads.sync_error); it never stops the site.
    """
    if store is None:
        url = database_url()
        if not url:
            return
        store = PgStore(url)
    ads.db = True
    lock = threading.Lock()

    def push() -> None:
        with lock:
            store.put(build_backup(ads, image_dir))

    last = None
    for attempt in range(3):                              # a sleeping free database can need a moment to wake
        try:
            raw = store.get()
            if raw:
                ok, msg = restore_backup(ads, image_dir, raw)
                if not ok:
                    raise RuntimeError(msg)
            elif ads.path and ads.path.is_file():         # first run: the repo's ads.json becomes the starting set
                push()
            last = None
            break
        except Exception as exc:                          # noqa: BLE001 - report, never crash the site
            last = exc
            time.sleep(2 * (attempt + 1))
    if last is not None:
        ads.sync_error = f"Could not reach the ads database: {last}"
        return                                            # do NOT start pushing: it could overwrite saved ads with an empty set
    ads.on_save = push
