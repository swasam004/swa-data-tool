"""Advertising slots for the web interface.

Manage ads from the admin panel (/admin) - or edit ads.json by hand (no restart needed). Four slots:

  top           SMALL strip under the header on the home page      (468 x 60)
  home_below    large banner under the upload box                  (970 x 250)
  result_below  banner under a finished report                     (728 x 120)
  footer        banner above the footer on every page              (728 x 90)

Each slot is a LIST of ads (a random live one is shown per page view).
Ad types:
  image : {"type":"image","src":"/ads-media/file.png" or "https://...","href":"https://advertiser","alt":"Advertiser"}
  text  : {"type":"text","title":"...","body":"...","href":"https://...","cta":"Learn more"}
  html  : {"type":"html","html":"<script>...</script>","width":300,"height":250}
          Script ads run inside a SANDBOXED frame: they cannot read visitors' uploads, reports or cookies.
Optional on any ad: "start":"2026-12-01", "end":"2026-12-31", "enabled":false (paused), "label":"Sponsored", "note":"internal".
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import os
import random
import re
import secrets
import zipfile
from html import escape
from pathlib import Path
from urllib.parse import urlparse

SLOTS = {"top": (468, 60), "home_below": (970, 250), "result_below": (728, 120), "footer": (728, 90)}
SLOT_INFO = {
    "top": ("Top strip", "Small strip under the header, home page only"),
    "home_below": ("Large banner", "Directly under the upload box, home page"),
    "result_below": ("Results banner", "Under a finished report"),
    "footer": ("Footer banner", "Bottom of every page"),
}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
UPLOAD_EXT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
TYPES = {"image", "text", "html"}
MAX_IMG_BYTES = 600 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,100}$")


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

    # ------------------------------------------------------------------ reading
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
            if ad.get("enabled") is False:
                continue
            s, e = _date(ad.get("start")), _date(ad.get("end"))
            if (s and today < s) or (e and today > e):
                continue
            out.append(ad)
        return out

    def live_count(self, slot: str) -> int:
        return sum(1 for ad in self._active(slot) if self._render_ad(ad, *SLOTS[slot]))

    # ------------------------------------------------------------------ rendering
    def render(self, slot: str) -> str:
        self._load()
        w, h = SLOTS[slot]
        candidates = []
        for ad in self._active(slot):                    # only ads that render safely take part in the rotation
            inner = self._render_ad(ad, w, h)
            if inner:
                candidates.append((ad, inner))
        if candidates:
            ad, inner = random.choice(candidates)
            label = escape(str(ad.get("label") or "Advertisement"))
            return f"<aside class='ad ad-{slot}' style='--w:{w}px' aria-label='{label}'><small>{label}</small>{inner}</aside>"
        return ""                                          # empty slot: visitors see nothing

    def advertise_html(self) -> str:
        """The ONE 'Advertise with us' link (shown in the footer). Switch off with placeholders=false in ads.json."""
        self._load()
        if not self._data.get("placeholders", True):
            return ""
        return f"<a class='advlink' href='{escape(self.contact, quote=True)}' target='_blank' rel='noopener'>Advertise with us</a>"

    @staticmethod
    def _render_ad(ad: dict, w: int, h: int) -> str:
        kind = ad["type"]
        if kind == "image":
            src = str(ad.get("src", ""))
            href = safe_url(ad.get("href"))
            local = src.startswith("/ads-media/") and NAME_RE.match(src[11:]) and Path(src).suffix.lower() in IMG_EXT
            remote = safe_url(src) and Path(urlparse(src).path).suffix.lower() in IMG_EXT
            if not (local or remote):
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
        try:
            fw = max(1, min(int(ad.get("width") or w), 2000))
            fh = max(1, min(int(ad.get("height") or h), 2000))
        except (TypeError, ValueError):
            fw, fh = w, h
        doc = ("<!doctype html><meta charset='utf-8'><base target='_blank'>"
               "<style>html,body{margin:0;height:100%}</style><body>") + code
        return (f"<iframe class='adframe' title='Advertisement' width='{fw}' height='{fh}' loading='lazy' "
                f"referrerpolicy='no-referrer' sandbox='allow-scripts allow-popups allow-popups-to-escape-sandbox' "
                f"srcdoc='{escape(doc, quote=True)}' style='max-width:100%'></iframe>")

    # ------------------------------------------------------------------ editing (admin panel)
    def raw(self) -> dict:
        """A normalised COPY of the ads file: every slot is a list."""
        self._load()
        d = json.loads(json.dumps(self._data)) if self._data else {}
        d["contact"] = d["contact"] if isinstance(d.get("contact"), str) else "https://swasamson.site"
        d["placeholders"] = bool(d.get("placeholders", True))
        slots = d.get("slots") if isinstance(d.get("slots"), dict) else {}
        d["slots"] = {}
        for k in SLOTS:
            v = slots.get(k)
            d["slots"][k] = [a for a in (v if isinstance(v, list) else ([v] if isinstance(v, dict) else [])) if isinstance(a, dict)]
        return d

    def ensure_ids(self) -> dict:
        d = self.raw()
        changed = any(not ad.get("id") for lst in d["slots"].values() for ad in lst)
        if changed:
            for lst in d["slots"].values():
                for ad in lst:
                    ad.setdefault("id", secrets.token_hex(4))
            self.save(d)
        return d

    def save(self, data: dict) -> None:
        if not self.path:
            raise OSError("no ads file configured")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        self._mtime = None            # force reload

    @staticmethod
    def validate(ad: dict, image_dir: Path | None = None) -> str | None:
        """Return an error message, or None when the ad is acceptable."""
        t = ad.get("type")
        if t not in TYPES:
            return "Choose an ad type."
        for k in ("start", "end"):
            if ad.get(k) and not _date(ad[k]):
                return f"The {k} date must look like 2026-12-31."
        if ad.get("start") and ad.get("end") and _date(ad["start"]) > _date(ad["end"]):
            return "The end date is before the start date."
        if ad.get("href") and not safe_url(ad["href"]):
            return "The link must start with http:// or https://"
        if t == "image":
            src = str(ad.get("src", ""))
            local = src.startswith("/ads-media/") and NAME_RE.match(src[11:]) and Path(src).suffix.lower() in IMG_EXT
            remote = safe_url(src) and Path(urlparse(src).path).suffix.lower() in IMG_EXT
            if not (local or remote):
                return "Add an image: upload a PNG/JPG/WEBP file, or give an https link that ends in .png/.jpg/.webp."
            if local and image_dir and not (image_dir / src[11:]).is_file():
                return "That image file was not found."
        elif t == "text":
            if not str(ad.get("title", "")).strip():
                return "A text ad needs a headline."
            if not safe_url(ad.get("href")):
                return "A text ad needs a link starting with https://"
        else:
            if not str(ad.get("html", "")).strip():
                return "Paste the embed code."
            if len(str(ad["html"])) > 20000:
                return "The embed code is too long (limit 20,000 characters)."
        return None

    @staticmethod
    def status(ad: dict) -> str:
        if ad.get("enabled") is False:
            return "Paused"
        today = _dt.date.today()
        s, e = _date(ad.get("start")), _date(ad.get("end"))
        if s and today < s:
            return "Scheduled"
        if e and today > e:
            return "Expired"
        return "Live"


# ---------------------------------------------------------------------- images & backups
def process_image_upload(data: bytes, image_dir: Path):
    """Verify and RE-ENCODE an uploaded banner (this strips hidden payloads). Returns (url_path|None, message, size|None)."""
    from PIL import Image, UnidentifiedImageError
    if not data:
        return None, "No image was selected.", None
    if len(data) > MAX_IMG_BYTES:
        return None, f"The image is larger than {MAX_IMG_BYTES // 1024} KB. Please compress it.", None
    try:
        probe = Image.open(io.BytesIO(data))
        fmt, size = probe.format, probe.size
        probe.verify()
        if fmt not in UPLOAD_EXT:
            return None, "Only PNG, JPG or WEBP images are accepted.", None
        if not (20 <= size[0] <= 3000 and 20 <= size[1] <= 3000):
            return None, "Image dimensions must be between 20 and 3000 pixels.", None
        im = Image.open(io.BytesIO(data))
        im.load()
        if fmt == "JPEG" and im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format=fmt, **({"quality": 90, "optimize": True} if fmt in ("JPEG", "WEBP") else {"optimize": True}))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None, "That file is not a valid image.", None
    image_dir.mkdir(parents=True, exist_ok=True)
    name = f"ad-{secrets.token_hex(6)}{UPLOAD_EXT[fmt]}"
    (image_dir / name).write_bytes(out.getvalue())
    return f"/ads-media/{name}", "ok", size


def build_backup(store: Ads, image_dir: Path) -> bytes:
    d = store.raw()
    used = {Path(ad.get("src", "")).name for lst in d["slots"].values() for ad in lst
            if str(ad.get("src", "")).startswith("/ads-media/")}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ads.json", json.dumps(d, indent=2, ensure_ascii=False))
        for n in sorted(used):
            f = image_dir / n
            if NAME_RE.match(n) and f.is_file():
                z.write(f, f"ads/{n}")
    return buf.getvalue()


def restore_backup(store: Ads, image_dir: Path, raw: bytes):
    """Restore from a backup .zip (ads.json + ads/ images) or a bare ads.json. Everything is re-validated."""
    images: dict[str, bytes] = {}
    try:
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                infos = z.infolist()
                if len(infos) > 120 or sum(i.file_size for i in infos) > 30 * 1024 * 1024:
                    return False, "The backup is too large."
                if "ads.json" not in z.namelist():
                    return False, "This zip has no ads.json inside."
                data = json.loads(z.read("ads.json").decode("utf-8-sig"))
                for i in infos:
                    n = i.filename
                    if n.startswith("ads/") and NAME_RE.match(n[4:]) and Path(n).suffix.lower() in IMG_EXT and i.file_size <= 1024 * 1024:
                        images[n[4:]] = z.read(i)
        else:
            data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, zipfile.BadZipFile, UnicodeDecodeError):
        return False, "That file could not be read as an ads backup."
    if not isinstance(data, dict):
        return False, "The backup content is not valid."
    from PIL import Image
    good: dict[str, bytes] = {}
    for n, b in images.items():
        try:
            Image.open(io.BytesIO(b)).verify()
            good[n] = b
        except Exception:
            pass
    kept = dropped = 0
    new = {"contact": data["contact"] if isinstance(data.get("contact"), str) else "https://swasamson.site",
           "placeholders": bool(data.get("placeholders", True)), "slots": {}}
    for slot, lst in (data.get("slots") or {}).items():
        if slot not in SLOTS:
            continue
        new["slots"][slot] = []
        for ad in (lst if isinstance(lst, list) else [lst]):
            if not isinstance(ad, dict):
                dropped += 1
                continue
            src = str(ad.get("src", ""))
            if src.startswith("/ads-media/") and src[11:] not in good and not (image_dir / src[11:]).is_file():
                dropped += 1
                continue
            if Ads.validate(ad) is not None:
                dropped += 1
                continue
            ad = dict(ad)
            ad.setdefault("id", secrets.token_hex(4))
            new["slots"][slot].append(ad)
            kept += 1
    image_dir.mkdir(parents=True, exist_ok=True)
    for n, b in good.items():
        (image_dir / n).write_bytes(b)
    store.save(new)
    return True, f"Restored {kept} ad(s)" + (f"; {dropped} invalid ad(s) were skipped." if dropped else ".")
