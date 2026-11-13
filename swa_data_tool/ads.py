"""Advertising slots for the web interface.

Edit `ads.json` (next to config.yaml) - no restart needed. Four slots exist:

  top           wide banner under the header on the home page      (728 x 90)
  home_below    large banner under the upload box                  (970 x 250)
  result_below  banner under a finished report                     (728 x 120)
  footer        banner above the footer on every page              (728 x 90)

Each slot is ONE ad or a LIST of ads (a random one is shown per page view).
Ad types:
  image : {"type":"image","src":"/ads-media/file.png" or "https://...","href":"https://advertiser","alt":"Advertiser"}
  text  : {"type":"text","title":"...","body":"...","href":"https://...","cta":"Learn more"}
  html  : {"type":"html","html":"<script>...</script>","width":300,"height":250}
          Script ads run inside a SANDBOXED frame: they cannot read your visitors' uploads, reports or cookies.
Optional on any ad: "start":"2026-12-01", "end":"2026-12-31" (shown only between these dates), "label":"Sponsored".
"""
from __future__ import annotations

import datetime as _dt
import json
import random
from html import escape
from pathlib import Path
from urllib.parse import urlparse

SLOTS = {"top": (728, 90), "home_below": (970, 250), "result_below": (728, 120), "footer": (728, 90)}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TYPES = {"image", "text", "html"}


def safe_url(u) -> str | None:
    """Only absolute http(s) links (blocks javascript: and data: URLs)."""
    if not isinstance(u, str):
        return None
    p = urlparse(u.strip())
    return u.strip() if p.scheme in ("http", "https") and p.netloc else None


def _date(v):
    try:
        return _dt.date.fromisoformat(str(v))
    except ValueError:
        return None


class Ads:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self._mtime = None
        self._data: dict = {}
        self.problems: list[str] = []

    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            self._data, self._mtime = {}, None
            return
        mtime = self.path.stat().st_mtime
        if mtime == self._mtime:
            return
        self._mtime = mtime
        self.problems = []
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(self._data, dict):
                raise ValueError("top level must be an object")
        except (ValueError, OSError) as exc:
            self._data = {}
            self.problems.append(f"ads.json could not be read: {exc}")

    @property
    def contact(self) -> str:
        self._load()
        c = self._data.get("contact", "")
        return c if (isinstance(c, str) and (c.startswith("mailto:") or safe_url(c))) else "https://swasamson.site"

    def _active(self, slot: str) -> list[dict]:
        self._load()
        raw = (self._data.get("slots") or {}).get(slot)
        ads = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        today, out = _dt.date.today(), []
        for ad in ads:
            if not isinstance(ad, dict) or ad.get("type") not in TYPES:
                continue
            s, e = _date(ad.get("start")), _date(ad.get("end"))
            if (s and today < s) or (e and today > e):
                continue
            out.append(ad)
        return out

    def render(self, slot: str) -> str:
        self._load()
        w, h = SLOTS[slot]
        placeholders = self._data.get("placeholders", True)
        candidates = []
        for ad in self._active(slot):                    # only ads that render safely take part in the rotation
            inner = self._render_ad(ad, w, h)
            if inner:
                candidates.append((ad, inner))
        if candidates:
            ad, inner = random.choice(candidates)
            label = escape(str(ad.get("label") or "Advertisement"))
            return f"<aside class='ad' style='--w:{w}px' aria-label='{label}'><small>{label}</small>{inner}</aside>"
        if not placeholders:
            return ""
        contact = escape(self.contact, quote=True)
        return (f"<aside class='ad' style='--w:{w}px' aria-label='Advertising space'><small>Advertisement</small>"
                f"<a class='ph' style='aspect-ratio:{w}/{h}' href='{contact}' target='_blank' rel='noopener'>"
                f"<span class='ph-t'>Your ad could be here</span><span class='ph-s'>{w} &times; {h} &middot; reach NGOs, researchers and data teams</span>"
                f"<span class='ph-c'>Advertise with us</span></a></aside>")

    @staticmethod
    def _render_ad(ad: dict, w: int, h: int) -> str:
        kind = ad["type"]
        if kind == "image":
            src = str(ad.get("src", ""))
            href = safe_url(ad.get("href"))
            ok_src = (src.startswith("/ads-media/") and Path(src).suffix.lower() in IMG_EXT and ".." not in src) or \
                (safe_url(src) and Path(urlparse(src).path).suffix.lower() in IMG_EXT)
            if not ok_src:
                return ""
            img = f"<img src='{escape(src, quote=True)}' alt='{escape(str(ad.get('alt', 'Advertisement')), quote=True)}' loading='lazy'>"
            return (f"<a href='{escape(href, quote=True)}' target='_blank' rel='sponsored nofollow noopener'>{img}</a>" if href else img)
        if kind == "text":
            href = safe_url(ad.get("href"))
            title, body = escape(str(ad.get("title", ""))[:80]), escape(str(ad.get("body", ""))[:220])
            cta = escape(str(ad.get("cta", "Learn more"))[:30])
            if not title or not href:
                return ""
            return (f"<a class='tad' href='{escape(href, quote=True)}' target='_blank' rel='sponsored nofollow noopener'>"
                    f"<b>{title}</b><span>{body}</span><em>{cta} &rarr;</em></a>")
        # html: sandboxed frame without allow-same-origin => cannot touch the site's cookies, pages or uploads
        code = str(ad.get("html", ""))
        if not code.strip():
            return ""
        fw = int(ad.get("width") or w)
        fh = int(ad.get("height") or h)
        doc = ("<!doctype html><meta charset='utf-8'><base target='_blank'>"
               "<style>html,body{margin:0;height:100%}</style><body>") + code
        return (f"<iframe class='adframe' title='Advertisement' width='{fw}' height='{fh}' loading='lazy' "
                f"referrerpolicy='no-referrer' sandbox='allow-scripts allow-popups allow-popups-to-escape-sandbox' "
                f"srcdoc='{escape(doc, quote=True)}' style='max-width:100%'></iframe>")
