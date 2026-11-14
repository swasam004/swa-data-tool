"""Owner-only admin area: setup checklist, ads manager, backups and the settings editor."""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import tempfile
import time
from html import escape
from pathlib import Path

from flask import Response, g, redirect, request

from .ads import SLOT_INFO, SLOTS, Ads, build_backup, process_image_upload, restore_backup, safe_url
from .config import ConfigError, load_config
from .webui import ICON

PLACEHOLDER_SALT = "replace-this-with-a-long-secret-phrase"
ADMIN_CSS = """
.adm{max-width:1040px;margin:0 auto}.adm .panel{margin:20px 0}
.adm h2{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.adm h2 small{font-weight:600;color:var(--mut);font-size:14px}
.chk{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;border-radius:14px;border:1px solid var(--line);background:rgba(255,255,255,.04);margin:9px 0}
.chk i{flex:none;width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-style:normal;font-weight:800;font-size:14px}
.chk.ok i{background:rgba(52,211,153,.18);color:var(--ok)}.chk.warn i{background:rgba(251,191,36,.18);color:var(--warn)}.chk.info i{background:rgba(34,211,238,.18);color:var(--cyan)}
.chk b{display:block}.chk span{color:var(--mut);font-size:14.5px}
.adrow{display:flex;gap:16px;align-items:center;padding:14px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.04);margin:10px 0;flex-wrap:wrap}
.adprev{width:210px;flex:none;max-width:100%}.adprev img{width:100%;border-radius:8px;display:block}.adprev .box{border:1px dashed var(--line);border-radius:10px;padding:10px;font-size:13.5px;color:var(--mut);word-break:break-word}
.adinfo{flex:1;min-width:200px;font-size:14.5px;color:#cdd9f6}.adinfo b{color:#fff}.adinfo small{display:block;color:var(--mut)}
.badge{display:inline-block;font-size:12px;font-weight:800;padding:2px 10px;border-radius:99px;border:1px solid currentColor;margin-right:6px}
.badge.Live{color:var(--ok)}.badge.Paused{color:var(--mut)}.badge.Scheduled{color:var(--cyan)}.badge.Expired{color:var(--bad)}
.acts{display:flex;gap:8px;flex-wrap:wrap}.acts form{margin:0}
.btn.danger{background:linear-gradient(135deg,#e5484d,#b4233a);box-shadow:0 8px 20px rgba(220,60,70,.35)}
details.addad{margin-top:12px}details.addad>summary{list-style:none;display:inline-flex}details.addad>summary::-webkit-details-marker{display:none}
.addform{margin-top:14px;padding:18px;border:1px solid var(--line);border-radius:18px;background:rgba(2,6,20,.35)}
.fgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}.fgrid .wide{grid-column:1/-1}
label.f{display:block;font-weight:700;font-size:14px;color:#cfdcff}label.f small{font-weight:500;color:var(--mut)}
input.t,select.t,textarea.t{width:100%;margin-top:6px;background:rgba(2,6,20,.65);color:#eaf0ff;border:1px solid var(--line);border-radius:12px;padding:11px 13px;font:500 15px var(--font)}
input.t:focus,select.t:focus,textarea.t:focus{outline:2px solid rgba(34,211,238,.6);border-color:transparent}textarea.t{min-height:110px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13.5px}
input.t,select.t,textarea.t{color-scheme:dark}input[type=file].t{padding:9px}.flash{border-radius:16px;padding:13px 18px;margin:18px 0 0;font-weight:700}.flash.ok{border:1.5px solid var(--ok);background:rgba(52,211,153,.12);color:#b6f5dc}.flash.bad{border:1.5px solid var(--bad);background:rgba(251,113,133,.12);color:#ffd0d8}
.loginbox{max-width:440px;margin:30px auto}.hint{color:var(--mut);font-size:14px;margin:6px 0 0}.topbtns{display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:14px}
.cb{display:flex;gap:10px;align-items:center;font-weight:600;color:#cfdcff}.cb input{width:18px;height:18px}
"""


def register_admin(app, X):
    """X = shared helpers from web.create_app (render, token, csrf_ok, ads, ...)."""
    ads: Ads = X["ads"]
    ads_dir: Path = X["ads_dir"]
    admin_pw: str = X["admin_pw"]
    secret: bytes = X["secret"]
    fails: dict[str, list[float]] = {}

    # ------------------------------------------------------------------ auth (login form + signed cookie)
    def _key() -> bytes:
        import hashlib
        return hashlib.sha256(secret + b"|" + admin_pw.encode()).digest()

    def _sig(exp: int) -> str:
        import hashlib
        import hmac
        return hmac.new(_key(), f"admin|{exp}".encode(), hashlib.sha256).hexdigest()

    def cookie_valid() -> bool:
        if not admin_pw:
            return False
        try:
            exp, sig = request.cookies.get("swa_admin", "").split(".", 1)
            return int(exp) > time.time() and secrets.compare_digest(sig, _sig(int(exp)))
        except (ValueError, AttributeError):
            return False

    def client_ip() -> str:
        return (request.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or request.remote_addr or "?")

    def require_admin():
        if X["admin_ok"]():
            return None
        if admin_pw:
            return redirect("/admin/login")
        return X["message"]("Admin is locked", "Set the SWA_ADMIN_PASSWORD environment variable on the server to enable the admin area.", 403)

    def flash(msg: str, kind: str = "ok"):
        g.flash = (msg, kind)

    def page(title, body):
        fl = ""
        raw = request.cookies.get("swa_flash")
        if raw:
            try:
                msg, kind = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
                fl = f"<div class='flash {'ok' if kind == 'ok' else 'bad'}' role='status'>{escape(str(msg))}</div>"
            except Exception:
                pass
            g.clear_flash = True
        return X["render"](title, f"<style>{ADMIN_CSS}</style><div class='adm'>{fl}{body}</div>", active="admin", noindex=True)

    def go(path="/admin"):
        return redirect(path, code=303)

    @app.after_request
    def _flash_cookie(r):
        if getattr(g, "flash", None):
            r.set_cookie("swa_flash", base64.urlsafe_b64encode(json.dumps(g.flash).encode()).decode(), max_age=60, httponly=True, samesite="Lax")
        elif getattr(g, "clear_flash", False):
            r.delete_cookie("swa_flash")
        return r

    # ------------------------------------------------------------------ login / logout
    @app.get("/admin/login")
    def login_form():
        if X["admin_ok"]() and admin_pw:
            return go()
        if not admin_pw:
            return X["message"]("Admin is locked", "Set the SWA_ADMIN_PASSWORD environment variable on the server to enable the admin area.", 403)
        body = f"""<section class='pagehead'><h1>Admin sign in</h1><p>Owner access only.</p></section>
<div class='panel loginbox'><form method='post' action='/admin/login'>
<input type='hidden' name='csrf' value='{X['token']()}'>
<label class='f'>Admin password<input class='t' type='password' name='password' autocomplete='current-password' autofocus required></label>
<p style='margin-top:18px'><button class='btn' style='width:100%'>Sign in</button></p></form></div>"""
        return page("Admin sign in", body)

    @app.post("/admin/login")
    def login_post():
        if not admin_pw:
            return redirect("/admin/login")
        if not X["csrf_ok"]():
            flash("Your session expired. Please try again.", "bad")
            return go("/admin/login")
        ip, now = client_ip(), time.time()
        recent = [t for t in fails.get(ip, []) if now - t < 600]
        fails[ip] = recent
        if len(recent) >= 5:
            flash("Too many wrong attempts. Wait 10 minutes and try again.", "bad")
            return go("/admin/login")
        if not secrets.compare_digest(request.form.get("password", ""), admin_pw):
            fails[ip].append(now)
            time.sleep(0.6)
            flash("That password is not correct.", "bad")
            return go("/admin/login")
        fails.pop(ip, None)
        exp = int(now) + 8 * 3600
        r = go("/admin")
        secure = request.is_secure or request.headers.get("X-Forwarded-Proto", "") == "https"
        r.set_cookie("swa_admin", f"{exp}.{_sig(exp)}", max_age=8 * 3600, httponly=True, samesite="Strict", secure=secure)
        return r

    @app.post("/admin/logout")
    def logout():
        r = go("/")
        r.delete_cookie("swa_admin")
        return r

    # ------------------------------------------------------------------ dashboard
    def checklist() -> str:
        items = []

        def add(kind, title, text):
            sym = {"ok": "&#10003;", "warn": "!", "info": "i"}[kind]
            items.append(f"<div class='chk {kind}'><i>{sym}</i><div><b>{escape(title)}</b><span>{text}</span></div></div>")

        c_default = None
        try:
            c_default = X["cfg"]()
        except ConfigError as exc:
            add("warn", "Settings have a problem", escape(str(exc)))
        uses_hash = False
        for _, (label, path) in X["profiles"]().items():
            try:
                if any(col.get("pii") == "hash" for col in X["cfg"](path)["columns"]):
                    uses_hash = True
            except ConfigError:
                pass
        if not uses_hash:
            add("ok", "Privacy salt", "Not needed: none of your analysis profiles hash personal data.")
        elif c_default and c_default["privacy_salt"] not in ("", PLACEHOLDER_SALT):
            add("ok", "Privacy salt is set", "Personal data is hashed with your secret phrase.")
        else:
            add("warn", "Privacy salt is not set", "Your profiles hash personal data. In Render &rarr; Environment add <code>SWA_PRIVACY_SALT</code> with a long secret phrase.")
        add("ok" if admin_pw else "warn", "Admin password", "Set." if admin_pw else "Not set. Add SWA_ADMIN_PASSWORD in Render &rarr; Environment.")
        if X["persistent"]:
            add("ok", "Ads storage is permanent", f"Saved in <code>{escape(str(ads.path))}</code>.")
        elif os.environ.get("RENDER"):
            add("warn", "Ads storage is temporary", "On Render's free plan, ads you add here are lost when the service restarts or redeploys. "
                "Download a backup after each change (Backup section) and restore it if needed, or attach a Render disk and set <code>SWA_DATA_DIR</code>.")
        else:
            add("info", "Ads storage", f"Saved in <code>{escape(str(ads.path))}</code>.")
        add("info", "Results retention", (f"Visitor results are deleted after {X['retention']} hours." if X["retention"] else "Results are kept until you delete them."))
        if ads.contact == "https://swasamson.site":
            add("info", "Advertiser contact link", "Currently points to swasamson.site. Set an e-mail below (for example <code>mailto:you@example.com</code>) so advertisers can reach you.")
        for p in ads.problems:
            add("warn", "Ads file problem", escape(p))
        return "".join(items)

    def preview(ad: dict) -> str:
        t = ad.get("type")
        if t == "image":
            src = escape(str(ad.get("src", "")), quote=True)
            return f"<img src='{src}' alt='' loading='lazy'>"
        if t == "text":
            return f"<div class='box'><b style='color:#fff'>{escape(str(ad.get('title', ''))[:60])}</b><br>{escape(str(ad.get('body', ''))[:90])}</div>"
        return f"<div class='box'>Embed code &middot; {escape(str(ad.get('width', '')))}&times;{escape(str(ad.get('height', '')))}</div>"

    def slot_panel(slot: str, lst: list) -> str:
        w, h = SLOTS[slot]
        title, where = SLOT_INFO[slot]
        tok = X["token"]()
        rows = ""
        for ad in lst:
            st = Ads.status(ad)
            dates = " &middot; ".join(x for x in (f"from {escape(str(ad['start']))}" if ad.get("start") else "", f"until {escape(str(ad['end']))}" if ad.get("end") else "") if x)
            link = safe_url(ad.get("href"))
            note = f"<small>Note: {escape(str(ad['note'])[:120])}</small>" if ad.get("note") else ""
            rows += f"""<div class='adrow'><div class='adprev'>{preview(ad)}</div>
<div class='adinfo'><span class='badge {st}'>{st}</span><b>{escape(str(ad.get('type', '')).title())} ad</b>
<small>{dates or 'No schedule - always on'}</small>{('<small>' + escape(link[:70]) + '</small>') if link else ''}{note}</div>
<div class='acts'>
<form method='post' action='/admin/ads/toggle'><input type='hidden' name='csrf' value='{tok}'><input type='hidden' name='slot' value='{slot}'><input type='hidden' name='id' value='{escape(str(ad.get('id', '')), quote=True)}'><button class='btn sm ghost'>{'Resume' if ad.get('enabled') is False else 'Pause'}</button></form>
<form method='post' action='/admin/ads/delete' onsubmit="return confirm('Delete this ad permanently?')"><input type='hidden' name='csrf' value='{tok}'><input type='hidden' name='slot' value='{slot}'><input type='hidden' name='id' value='{escape(str(ad.get('id', '')), quote=True)}'><button class='btn sm danger'>Delete</button></form></div></div>"""
        if not rows:
            rows = "<p class='hint'>No ads yet - nothing is shown to visitors in this slot.</p>"
        return f"""<div class='panel' id='slot-{slot}'><h2>{escape(title)} <small>{w}&times;{h} &middot; {escape(where)} &middot; {ads.live_count(slot)} live</small></h2>
{rows}
<details class='addad'><summary class='btn sm'>{ICON['plus']} Add an ad</summary>
<form class='addform' method='post' action='/admin/ads/add' enctype='multipart/form-data'>
<input type='hidden' name='csrf' value='{tok}'><input type='hidden' name='slot' value='{slot}'>
<div class='fgrid'>
<label class='f wide'>Ad type<select class='t adtype' name='type'><option value='image'>Image banner</option><option value='text'>Text ad</option><option value='html'>Embed / script code</option></select></label>
<div class='g-image wide fgrid'>
<label class='f'>Upload banner <small>PNG, JPG or WEBP, up to 600 KB &middot; best size {w}&times;{h}</small><input class='t' type='file' name='image' accept='image/png,image/jpeg,image/webp'></label>
<label class='f'>...or image link <small>https://... ending in .png/.jpg/.webp</small><input class='t' type='url' name='image_url' placeholder='https://'></label>
<label class='f'>Alt text <small>describes the banner</small><input class='t' name='alt' maxlength='120'></label></div>
<div class='g-text wide fgrid'>
<label class='f'>Headline<input class='t' name='title' maxlength='80'></label>
<label class='f'>Button text<input class='t' name='cta' maxlength='30' placeholder='Learn more'></label>
<label class='f wide'>Description<input class='t' name='body' maxlength='220'></label></div>
<div class='g-html wide fgrid'>
<label class='f wide'>Embed code <small>runs in a sandbox - it cannot read visitor data</small><textarea class='t' name='html' spellcheck='false'></textarea></label>
<label class='f'>Width<input class='t' type='number' name='width' min='1' max='2000' value='{w}'></label>
<label class='f'>Height<input class='t' type='number' name='height' min='1' max='2000' value='{h}'></label></div>
<label class='f'>Link when clicked <small>https://...</small><input class='t' type='url' name='href' placeholder='https://'></label>
<label class='f'>Label <small>shown above the ad</small><input class='t' name='label' maxlength='30' placeholder='Advertisement'></label>
<label class='f'>Start date <small>optional</small><input class='t' type='date' name='start'></label>
<label class='f'>End date <small>optional</small><input class='t' type='date' name='end'></label>
<label class='f wide'>Private note <small>advertiser name, price... (never shown to visitors)</small><input class='t' name='note' maxlength='160'></label>
</div><p style='margin-top:16px'><button class='btn'>Save ad</button></p></form></details></div>"""

    @app.get("/admin")
    def dashboard():
        r = require_admin()
        if r:
            return r
        d = ads.ensure_ids()
        tok = X["token"]()
        total_live = sum(ads.live_count(s) for s in SLOTS)
        stats = (f"<div class='stats'><div class='stat'><b>{total_live}</b><span>ads live now</span></div>"
                 f"<div class='stat'><b>{sum(1 for s in SLOTS if ads.live_count(s) == 0)}</b><span>empty slots</span></div>"
                 f"<div class='stat'><b>{sum(len(v) for v in d['slots'].values())}</b><span>ads saved</span></div></div>")
        panels = "".join(slot_panel(s, d["slots"][s]) for s in SLOTS)
        ph_checked = "checked" if d["placeholders"] else ""
        body = f"""<section class='pagehead'><h1>Admin</h1><p>Manage advertising and site settings.</p>
<div class='topbtns'><a class='btn sm ghost' href='/' target='_blank'>View site {ICON['ext']}</a><a class='btn sm ghost' href='/admin/settings'>Analysis settings</a>
{'<form method="post" action="/admin/logout" style="margin:0"><button class="btn sm ghost">Sign out</button></form>' if admin_pw else ''}</div></section>
{stats}
<div class='panel'><h2>Setup checklist</h2>{checklist()}</div>
<h2 style='margin:34px 0 0;font-size:26px;letter-spacing:-.02em'>Ads manager</h2>
{panels}
<div class='panel'><h2>Advertiser contact &amp; empty slots</h2>
<form method='post' action='/admin/ads/options'><input type='hidden' name='csrf' value='{tok}'><div class='fgrid'>
<label class='f wide'>Contact link for the "Advertise with us" button <small>e-mail as mailto:you@example.com, or a page https://...</small>
<input class='t' name='contact' value='{escape(d['contact'], quote=True)}' maxlength='200'></label>
<label class='cb'><input type='checkbox' name='placeholders' value='1' {ph_checked}> Show an "Advertise with us" link in the site footer</label></div>
<p style='margin-top:14px'><button class='btn sm'>Save</button></p></form></div>
<div class='panel'><h2>Backup &amp; restore <small>keeps your ads safe</small></h2>
<p class='hint'>Download a backup after you change ads. If the server ever restarts with a fresh disk, restore it here.</p>
<div class='dl'><a class='btn sm' href='/admin/ads/backup'>{ICON['download']} Download backup (.zip)</a></div>
<form method='post' action='/admin/ads/restore' enctype='multipart/form-data' style='margin-top:16px'>
<input type='hidden' name='csrf' value='{tok}'><label class='f'>Restore from a backup <small>.zip (or ads.json) - replaces the current ads</small>
<input class='t' type='file' name='backup' accept='.zip,.json,application/zip,application/json' required></label>
<p style='margin-top:12px'><button class='btn sm ghost' onclick="return confirm('Replace all current ads with this backup?')">Restore</button></p></form></div>
<script>document.querySelectorAll('.addform').forEach(function(f){{var s=f.querySelector('.adtype');function u(){{['image','text','html'].forEach(function(k){{f.querySelector('.g-'+k).style.display=(s.value===k)?'':'none'}})}}s.addEventListener('change',u);u()}});</script>"""
        return page("Admin", body)

    # ------------------------------------------------------------------ ad actions
    def _guard():
        r = require_admin()
        if r:
            return r
        if not X["csrf_ok"]():
            flash("Your session expired. Please try again.", "bad")
            return go()
        return None

    def _find(d, slot, ad_id):
        lst = d["slots"].get(slot, [])
        for i, ad in enumerate(lst):
            if ad.get("id") == ad_id:
                return lst, i
        return None, -1

    def _unused_images_cleanup(d):
        used = {Path(a.get("src", "")).name for lst in d["slots"].values() for a in lst if str(a.get("src", "")).startswith("/ads-media/")}
        if ads_dir.is_dir():
            for f in ads_dir.glob("ad-*"):
                if f.name not in used:
                    try:
                        f.unlink()
                    except OSError:
                        pass

    @app.post("/admin/ads/add")
    def ad_add():
        r = _guard()
        if r:
            return r
        f = request.form
        slot = f.get("slot", "")
        if slot not in SLOTS:
            return go()
        t = f.get("type", "")
        ad = {"id": secrets.token_hex(4), "type": t}
        for k in ("label", "start", "end", "note"):
            if f.get(k, "").strip():
                ad[k] = f[k].strip()[:160]
        if f.get("href", "").strip():
            ad["href"] = f["href"].strip()[:500]
        note = ""
        if t == "image":
            up = request.files.get("image")
            if up and up.filename:
                url, msg, size = process_image_upload(up.read(700 * 1024 + 1), ads_dir)
                if not url:
                    flash(msg, "bad")
                    return go(f"/admin#slot-{slot}")
                ad["src"] = url
                w, h = SLOTS[slot]
                if abs(size[0] / size[1] - w / h) > 0.35 * (w / h):
                    note = f" Heads up: your image is {size[0]}x{size[1]}, the slot shape is {w}x{h}, so it may look stretched or small."
            else:
                ad["src"] = f.get("image_url", "").strip()
            ad["alt"] = f.get("alt", "").strip()[:120] or "Advertisement"
        elif t == "text":
            ad.update(title=f.get("title", "").strip()[:80], body=f.get("body", "").strip()[:220], cta=(f.get("cta", "").strip()[:30] or "Learn more"))
        elif t == "html":
            ad["html"] = f.get("html", "")[:20000]
            for k, dflt in (("width", SLOTS[slot][0]), ("height", SLOTS[slot][1])):
                try:
                    ad[k] = max(1, min(int(f.get(k) or dflt), 2000))
                except ValueError:
                    ad[k] = dflt
        err = Ads.validate(ad, ads_dir)
        if err:
            flash(err, "bad")
            return go(f"/admin#slot-{slot}")
        d = ads.ensure_ids()
        d["slots"][slot].append(ad)
        ads.save(d)
        flash("Ad saved." + note)
        return go(f"/admin#slot-{slot}")

    @app.post("/admin/ads/delete")
    def ad_delete():
        r = _guard()
        if r:
            return r
        d = ads.ensure_ids()
        lst, i = _find(d, request.form.get("slot", ""), request.form.get("id", ""))
        if lst is not None:
            lst.pop(i)
            ads.save(d)
            _unused_images_cleanup(d)
            flash("Ad deleted.")
        return go(f"/admin#slot-{request.form.get('slot', '')}")

    @app.post("/admin/ads/toggle")
    def ad_toggle():
        r = _guard()
        if r:
            return r
        d = ads.ensure_ids()
        lst, i = _find(d, request.form.get("slot", ""), request.form.get("id", ""))
        if lst is not None:
            lst[i]["enabled"] = lst[i].get("enabled") is False
            ads.save(d)
            flash("Ad resumed." if lst[i]["enabled"] else "Ad paused.")
        return go(f"/admin#slot-{request.form.get('slot', '')}")

    @app.post("/admin/ads/options")
    def ad_options():
        r = _guard()
        if r:
            return r
        contact = request.form.get("contact", "").strip()
        if not (contact.startswith("mailto:") and "@" in contact or safe_url(contact)):
            flash("The contact link must be an e-mail written as mailto:you@example.com, or a link starting with https://", "bad")
            return go()
        d = ads.ensure_ids()
        d["contact"] = contact[:200]
        d["placeholders"] = request.form.get("placeholders") == "1"
        ads.save(d)
        flash("Saved.")
        return go()

    @app.get("/admin/ads/backup")
    def ad_backup():
        r = require_admin()
        if r:
            return r
        data = build_backup(ads, ads_dir)
        return Response(data, mimetype="application/zip", headers={"Content-Disposition": f"attachment; filename=swa-ads-backup-{time.strftime('%Y%m%d')}.zip"})

    @app.post("/admin/ads/restore")
    def ad_restore():
        r = _guard()
        if r:
            return r
        up = request.files.get("backup")
        if not up or not up.filename:
            flash("Choose a backup file first.", "bad")
            return go()
        ok, msg = restore_backup(ads, ads_dir, up.read(32 * 1024 * 1024))
        flash(msg, "ok" if ok else "bad")
        return go()

    # ------------------------------------------------------------------ analysis settings (config.yaml)
    @app.route("/admin/settings", methods=["GET", "POST"])
    def settings():
        r = require_admin()
        if r:
            return r
        config_path = X["config_path"]
        note = ""
        text = Path(config_path).read_text(encoding="utf-8-sig")
        if request.method == "POST":
            if not X["csrf_ok"]():
                flash("Your session expired. Reload the page.", "bad")
                return go("/admin/settings")
            text = request.form.get("text", "")
            fd, tmp = tempfile.mkstemp(suffix=".yaml", dir=str(Path(config_path).parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                c = load_config(tmp)
                if request.form.get("action") == "save":
                    Path(config_path + ".bak").write_text(Path(config_path).read_text(encoding="utf-8-sig"), encoding="utf-8")
                    os.replace(tmp, config_path)
                    os.chmod(config_path, 0o600)
                    note = "<div class='flash ok'>Saved. New settings apply to the next analysis (previous version kept as config.yaml.bak).</div>"
                else:
                    note = f"<div class='flash ok'>Settings are valid: {len(c['columns'])} column(s), {len(c['pivots'])} pivot(s).</div>"
            except ConfigError as exc:
                note = f"<div class='flash bad'><b>Problem:</b> {escape(str(exc))}</div>"
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        body = f"""<section class='pagehead'><h1>Analysis settings</h1><p>This is your config.yaml. Each <code>source:</code> must match a column header in the data file exactly.</p>
<div class='topbtns'><a class='btn sm ghost' href='/admin'>&larr; Back to admin</a></div></section>
<div class='panel'>{note}<form method='post'><input type='hidden' name='csrf' value='{X['token']()}'>
<textarea class='code' name='text' spellcheck='false'>{escape(text)}</textarea>
<p class='dl'><button class='btn sm ghost' name='action' value='check'>Check</button><button class='btn sm' name='action' value='save'>Save</button></p></form></div>"""
        return page("Analysis settings", body)

    @app.get("/settings")
    def settings_old():
        return redirect("/admin/settings")

    X["cookie_valid"] = cookie_valid
