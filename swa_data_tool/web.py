"""Web interface: upload a data file in the browser, get the verified report back.

    swa-tool serve -c config.yaml            (then open http://127.0.0.1:8080)

Environment variables (all optional):
  SWA_PASSWORD         require this password for the WHOLE site (HTTP basic auth, any user name)
  SWA_ADMIN_PASSWORD   password for the admin area (/admin: ads manager + settings). Without it the admin area is only
                       available from the same computer.
  SWA_DATA_DIR         folder for ads.json and ad images (point it at a persistent disk so ads survive restarts)
  SWA_SECRET           long random text used to sign forms (keeps forms valid across restarts)
  SWA_PRIVACY_SALT     secret phrase for hashing personal data (overrides privacy_salt in config.yaml - keeps it out of git)
  SWA_RETENTION_HOURS  delete results this many hours after creation (default 48; 0 = keep forever)
  SWA_MAX_MB           upload limit in MB (default 50)
  SWA_SITE_URL         public address, e.g. https://tools.swasamson.site (used for canonical links and the sitemap)
  SWA_GSC_VERIFICATION Google Search Console verification code (the content="..." value of the meta tag)
  SWA_DATABASE_URL     PostgreSQL link (free Neon/Supabase) - ads are saved there too, so they survive restarts on any plan
  SWA_ADS_FILE         path to ads.json (default: ads.json next to config.yaml)
  SWA_ADS_DIR          folder with ad images (default: ads/ next to config.yaml)
  SWA_PROFILES_DIR     folder with extra analysis profiles (*.yaml) shown in a drop-down (default: profiles/)
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from html import escape
from pathlib import Path

from flask import Flask, Response, abort, g, jsonify, redirect, request, send_file, send_from_directory

from .ads import IMG_EXT, Ads
from .ads_db import attach as attach_ads_db
from .config import ConfigError, load_config
from .pipeline import process_file
from .smart import load_qa_context, load_state, run_smart
from .smart_qa import ai_enabled, ask as smart_ask
from .smart_web import answer_card, _meta as smart_meta, smart_panels
from .webadmin import register_admin
from .webui import ICON, SITE, SITE_UTM, frame

DESCRIPTION = ("Upload a CSV, Excel or KoboToolbox file. Get verified pivot tables, charts and insights, and ask questions in plain English. "
               "Every figure is calculated twice.")
_lock = threading.Lock()          # one analysis at a time (the pipeline logs to one shared logger)
_purge = {"last": 0.0}
HERE = Path(__file__).resolve().parent
ALLOWED = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".json"}
RUN_ID = re.compile(r"^[A-Za-z0-9_\-]{1,200}$")
SID_RE = re.compile(r"^[a-f0-9]{32}$")
FILES = {"report.xlsx": "Excel report", "report.html": "HTML report", "clean_data.csv": "Clean data (CSV)",
         "quarantined_rows.csv": "Rejected rows (CSV)", "audit.json": "Audit record", "run.log": "Run log"}
_lock = threading.Lock()          # one analysis at a time (the pipeline logs to one shared logger)
_purge = {"last": 0.0}
HERE = Path(__file__).resolve().parent


def pretty(name: str) -> str:
    s = re.sub(r"[_\-]+", " ", name).strip()
    return (s[:1].upper() + s[1:]) if s else name


def purge_old(root: Path, hours: int) -> None:
    """Delete finished runs older than `hours` (and empty visitor folders)."""
    if not hours or not root.is_dir():
        return
    cutoff = time.time() - hours * 3600
    for sd in root.iterdir():
        if not sd.is_dir():
            continue
        for rd in sd.iterdir():
            try:
                if rd.is_dir() and rd.stat().st_mtime < cutoff:
                    shutil.rmtree(rd, ignore_errors=True)
            except OSError:
                pass
        try:
            if not any(sd.iterdir()):
                sd.rmdir()
        except OSError:
            pass


def create_app(config_path: str) -> Flask:
    config_path = str(Path(config_path).resolve())
    cdir = Path(config_path).parent
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("SWA_MAX_MB", "50")) * 1024 * 1024
    site_pw = os.environ.get("SWA_PASSWORD", "")
    admin_pw = os.environ.get("SWA_ADMIN_PASSWORD", "")
    secret = (os.environ.get("SWA_SECRET") or secrets.token_hex(32)).encode()
    retention = int(os.environ.get("SWA_RETENTION_HOURS", "48") or 0)
    data_dir = Path(os.environ.get("SWA_DATA_DIR") or cdir)
    persistent = bool(os.environ.get("SWA_DATA_DIR"))
    ads_file = Path(os.environ.get("SWA_ADS_FILE") or data_dir / "ads.json")
    ads_dir = Path(os.environ.get("SWA_ADS_DIR") or data_dir / "ads")
    if data_dir != cdir and not ads_file.exists() and (cdir / "ads.json").is_file():   # first run on a fresh data disk
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(cdir / "ads.json", ads_file)
    ads = Ads(ads_file)
    attach_ads_db(ads, ads_dir)          # no-op unless SWA_DATABASE_URL / DATABASE_URL is set
    profiles_dir = Path(os.environ.get("SWA_PROFILES_DIR") or cdir / "profiles")

    # ------------------------------------------------------------------ helpers
    def cfg(path: str | None = None):
        return load_config(path or config_path)

    def profiles() -> dict:
        out = {"smart": ("Smart analysis - any file, no setup", None), "standard": ("Structured register (uses settings)", config_path)}
        if profiles_dir.is_dir():
            for p in sorted(list(profiles_dir.glob("*.yaml")) + list(profiles_dir.glob("*.yml"))):
                key = re.sub(r"[^a-z0-9_\-]", "", p.stem.lower())
                if key and key not in out:
                    out[key] = (pretty(p.stem), str(p))
        return out

    def sessions_root() -> Path:
        return Path(cfg()["outputs"]) / "sessions"

    def my_dir() -> Path:
        return sessions_root() / g.sid

    def token() -> str:
        return hmac.new(secret, g.sid.encode(), hashlib.sha256).hexdigest()[:32]

    def csrf_ok() -> bool:
        return hmac.compare_digest(request.form.get("csrf", ""), token())

    def local_request() -> bool:
        return request.remote_addr in ("127.0.0.1", "::1") and not request.headers.get("X-Forwarded-For")

    def admin_ok() -> bool:
        if admin_pw:
            return bool(X["cookie_valid"]())
        if site_pw:
            return True            # already passed the site-wide password
        return local_request()

    def base_url() -> str:
        env = os.environ.get("SWA_SITE_URL", "").strip().rstrip("/")
        if env.startswith(("http://", "https://")):
            return env
        proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
        return f"{proto}://{request.headers.get('X-Forwarded-Host', request.host).split(',')[0].strip()}"

    def jsonld() -> str:
        data = {"@context": "https://schema.org", "@type": "WebApplication", "name": "SWA Data Analysis", "url": base_url() + "/",
                "description": DESCRIPTION, "applicationCategory": "BusinessApplication", "operatingSystem": "Any (web browser)",
                "browserRequirements": "Requires JavaScript", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
                "creator": {"@type": "Organization", "name": "swasamson.site", "url": SITE},
                "featureList": ["Works with any Excel or CSV file, no setup", "Ask questions in plain English", "Pivot tables and charts", "Programme indicators with disaggregation", "Integrity checks (every figure calculated twice)",
                                "Personal data masking", "KoboToolbox and ODK exports"]}
        return json.dumps(data).replace("</", "<\\/")

    def render(title, body, active="", noindex=False, home=False, result=False, status=200, index=False, description=""):
        top = ads.render("top") if home else ""
        public = (home or index) and not noindex and status == 200
        html = frame(title, body, active=active, show_admin=admin_ok(), top_ad=top,
                     footer_ad=ads.render("footer"), advertise=ads.advertise_html(), retention_hours=retention, noindex=not public, description=description,
                     canonical=(base_url() + ("/" if home else request.path)) if public else "", jsonld=jsonld() if home and public else "",
                     gsc=os.environ.get("SWA_GSC_VERIFICATION", ""))
        return Response(html, status=status, mimetype="text/html")

    def message(title, text, code=200, button=("/", "Back to start")):
        body = (f"<section class='pagehead'><h1>{escape(title)}</h1><p>{escape(text)}</p></section>"
                f"<p style='text-align:center'><a class='btn' href='{button[0]}'>{escape(button[1])}</a></p>")
        return render(title, body, status=code)

    # ------------------------------------------------------------------ request hooks
    @app.before_request
    def guard():
        if request.endpoint in ("healthz", "static_font", "robots", "sitemap"):
            return None
        if site_pw:
            a = request.authorization
            pw = (a.password or "") if a else ""
            if not secrets.compare_digest(pw, site_pw):
                return Response("Password required.", 401, {"WWW-Authenticate": 'Basic realm="SWA"'})
        sid = request.cookies.get("swa_sid", "")
        g.new_sid = None
        if not SID_RE.match(sid):
            sid = secrets.token_hex(16)
            g.new_sid = sid
        g.sid = sid
        if retention and time.time() - _purge["last"] > 600:
            _purge["last"] = time.time()
            try:
                purge_old(sessions_root(), retention)
            except Exception:       # cleanup must never break a request
                pass
        return None

    @app.after_request
    def headers(r):
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["X-Frame-Options"] = "SAMEORIGIN"
        r.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.endpoint not in ("static_font", "ads_media"):
            r.headers["Cache-Control"] = "no-store"
        if getattr(g, "new_sid", None):
            secure = request.is_secure or request.headers.get("X-Forwarded-Proto", "") == "https"
            r.set_cookie("swa_sid", g.new_sid, max_age=7 * 86400, httponly=True, samesite="Lax", secure=secure)
        return r

    @app.errorhandler(404)
    def not_found(_):
        return message("Page not found", "This page does not exist, or the result has expired or belongs to another browser.", 404)

    @app.errorhandler(413)
    def too_big(_):
        return message("That file is too large", f"The upload limit is {app.config['MAX_CONTENT_LENGTH'] // 1048576} MB.", 413)

    def run_dir(run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            abort(404)
        d = my_dir() / run_id
        if not d.is_dir():
            abort(404)
        return d

    # ------------------------------------------------------------------ static bits
    @app.get("/static/figtree.woff2", endpoint="static_font")
    def static_font():
        r = send_from_directory(HERE / "static", "figtree.woff2", mimetype="font/woff2")
        r.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return r

    @app.get("/ads-media/<name>", endpoint="ads_media")
    def ads_media(name):
        if not re.match(r"^[A-Za-z0-9._\-]{1,100}$", name) or Path(name).suffix.lower() not in IMG_EXT:
            abort(404)
        r = send_from_directory(ads_dir, name)
        r.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        r.headers["Cache-Control"] = "public, max-age=3600"
        return r

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/robots.txt", endpoint="robots")
    def robots():
        return Response("User-agent: *\nAllow: /\nDisallow: /runs/\nDisallow: /history\nDisallow: /settings\nDisallow: /admin\nDisallow: /analyse\n"
                        f"\nSitemap: {base_url()}/sitemap.xml\n", mimetype="text/plain")

    @app.get("/sitemap.xml", endpoint="sitemap")
    def sitemap():
        b = base_url()
        urls = "".join(f"<url><loc>{escape(b + p)}</loc></url>" for p in ("/", "/about", "/privacy"))
        return Response(f"<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>{urls}</urlset>", mimetype="application/xml")

    # ------------------------------------------------------------------ home
    @app.get("/")
    def home():
        try:
            c = cfg()
        except ConfigError as exc:
            if admin_ok():
                return message("The saved settings have a problem", str(exc), 500, ("/admin/settings", "Fix settings"))
            return message("This site is being updated", "Please try again in a few minutes.", 503)
        profs = profiles()
        maxmb = app.config["MAX_CONTENT_LENGTH"] // 1048576
        select, col_notes = "", ""
        if len(profs) > 1:
            select = ("<select class='prof' name='profile' id='prof' aria-label='Type of data'>"
                      + "".join(f"<option value='{escape(k)}'>{escape(v[0])}</option>" for k, v in profs.items()) + "</select>")
        for i, (k, (label, path)) in enumerate(profs.items()):
            if path is None:
                col_notes += f"<p data-prof='{escape(k)}'><b>{escape(label)}:</b> none needed. The table, its column names and types are found automatically, and a lookup sheet is joined if there is one. Questions written in the workbook are answered too.</p>"
                continue
            try:
                names = ", ".join(escape(str(x["name"])) for x in cfg(path)["columns"])
            except ConfigError:
                continue
            col_notes += f"<p data-prof='{escape(k)}'{' hidden' if i else ''}><b>{escape(label)}:</b> {names}</p>"
        body = f"""<section class='hero'>
<span class='eyebrow'><i></i> Verified data analysis for NGOs, researchers &amp; teams</span>
<h1 class='big'>Turn raw data into <span class='grad'>verified insights</span></h1>
<p class='lead'>Upload any Excel, CSV or JSON file &mdash; no setup. Get pivot tables, charts and insights, and ask questions in plain English. Every figure is checked twice.</p>
<a class='btn' id='cta' href='#drop'>{ICON['upload']} Upload your data</a>
<div class='trust'><span>{ICON['check2']} Every figure calculated twice</span><span>{ICON['shield']} Personal data can be masked</span><span>{ICON['check2']} File deleted right after analysis</span></div>
</section>
<section class='stage reveal' aria-label='Upload'>
<div class='chip c1'><small>Pivot tables</small><div class='bars'><i></i><i></i><i></i><i></i><i></i></div></div>
<div class='chip c2'><small>Integrity checks</small><b>&#10003; PASSED</b></div>
<div class='chip c3'><small>Ask a question</small>&ldquo;Average price per owner?&rdquo;</div>
<div class='chip c4'><small>Reports</small>Excel + HTML</div>
<form id='f' method='post' action='/analyse' enctype='multipart/form-data'>
<input type='hidden' name='csrf' value='{token()}'>
<div class='drop' id='drop' data-max='{app.config["MAX_CONTENT_LENGTH"]}'>
<div class='prompt' id='prompt'><span>Drop your CSV, Excel or JSON file here</span><i class='caret'></i></div>
<div class='file' id='chip'>{ICON['sheet']}<div><b id='fname'></b><small id='fsize'></small></div></div>
<input class='title-in' name='title' maxlength='100' placeholder='Name this analysis (optional) &mdash; e.g. Q3 Outreach Report' autocomplete='off' aria-label='Name this analysis (optional)'>
<details class='askbox'><summary>&#43; Ask questions about your data (optional)</summary>
<textarea name='questions' maxlength='3000' placeholder='One question per line, for example:&#10;What is the most popular category?&#10;What is the average price per owner?&#10;Is price related to km driven?' aria-label='Questions about your data'></textarea></details>
<div class='err' id='err' role='alert'></div>
<div class='row'>
<input id='file' name='file' type='file' class='sr-only' accept='.csv,.tsv,.txt,.xlsx,.xlsm,.xls,.json' required>
<label class='add' for='file' title='Choose a file' aria-label='Choose a file'>{ICON['plus']}</label>
<div class='fmt'><b>CSV</b><b>XLSX</b><b>JSON</b><span>up to {maxmb} MB</span></div>
{select}
<button class='go' id='go' disabled aria-label='Analyse my data'>{ICON['arrow']}</button>
</div></div></form>
<p class='note'>Private by design: your file is deleted as soon as the analysis ends{f' and results are removed after {retention} hours' if retention else ''}.</p>
<details class='cols'><summary>Which columns does my file need?</summary>{col_notes}</details>
</section>
{ads.render('home_below')}
<div class='overlay' id='overlay' role='status' aria-live='polite'><div class='loader'><i></i><i></i><i></i><i></i><i></i></div>
<h3>Analysing and verifying your data&hellip;</h3><p id='tip'>Rejected rows are listed with the reason, so nothing disappears silently.</p></div>
<section class='sec' id='features'><h2 class='reveal'>Built so you can trust the numbers</h2>
<p class='sub reveal'>Analysis mistakes are expensive. Every run is checked before you see it.</p>
<div class='grid4'>
<div class='card reveal'><div class='ico i1'>{ICON['check2']}</div><h3>Calculated twice</h3><p>Pivots, indicators and every answer are computed by two independent methods. Any disagreement stops the result.</p></div>
<div class='card reveal'><div class='ico i2'>{ICON['shield']}</div><h3>Privacy controls</h3><p>Hash, mask or drop names and phone numbers in every output, including rejected rows.</p></div>
<div class='card reveal'><div class='ico i3'>{ICON['kobo']}</div><h3>Any file, no setup</h3><p>Finds the table in messy Excel files, cleans it, joins lookup sheets and explains every change. Kobo and ODK exports work too.</p></div>
<div class='card reveal'><div class='ico i4'>{ICON['sheet']}</div><h3>Reports you can share</h3><p>Excel with live formulas and charts, plus a single-page HTML report for e-mail.</p></div>
</div></section>
<section class='sec' id='how'><h2 class='reveal'>From raw file to verified report in 3 steps</h2><p class='sub reveal'>No setup on your side &mdash; just your data.</p>
<div class='steps'>
<div class='card step reveal'><h3>Upload</h3><p>Drop a CSV, Excel or JSON file. Optionally give the analysis a name.</p></div>
<div class='card step reveal'><h3>We analyse &amp; verify</h3><p>Tables are found automatically, every change is logged, and every number is calculated twice.</p></div>
<div class='card step reveal'><h3>Ask &amp; download</h3><p>Ask questions in plain English, then download the Excel and HTML reports and the audit record.</p></div>
</div></section>
<section class='promo reveal'><div><h3>More from swasamson.site</h3><p>Tools, projects and resources for data, community programming and digital skills. Come and take a look.</p></div>
<a class='btn' href='{SITE_UTM}' target='_blank' rel='noopener'>Visit swasamson.site {ICON['ext']}</a></section>"""
        return render("Online data analysis tool for NGOs, researchers and teams", body, active="home", home=True, description=DESCRIPTION)

    # ------------------------------------------------------------------ about & privacy (public, indexable)
    @app.get("/about")
    def about():
        body = f"""<section class='pagehead'><h1>About SWA Data Analysis</h1><p>A data analysis tool built for NGOs, researchers and small teams.</p></section>
<div class='panel prose'>
<h2>What it does</h2>
<p>Upload a CSV, Excel or JSON file (including KoboToolbox and ODK exports). The tool cleans the data, sets aside rows that cannot be trusted,
and builds pivot tables, charts and programme indicators broken down by sex, age group, location and time. You download an Excel report,
an HTML report, the cleaned data and the list of rejected rows.</p>
<h2>Why you can trust the numbers</h2>
<ul><li>Every pivot table and indicator is calculated twice by independent methods. If they disagree, the report is marked as failed.</li>
<li>Rows are never silently changed or dropped: problem rows are listed with the reason.</li>
<li>Rows read always equal rows analysed plus rows set aside plus blank rows.</li>
<li>Excel results are live formulas, so Excel recalculates them for itself.</li></ul>
<h2>What it does not do</h2>
<p>It reports counts, sums, averages and percentages. It does not run significance tests, apply survey weights or correct for sampling.
Results are only as representative as the data you upload.</p>
<h2>Who is behind it</h2>
<p>SWA Data Analysis is built and run by <a href='{SITE_UTM}' target='_blank' rel='noopener'>swasamson.site</a>.</p>
<p><a class='btn' href='/'>{ICON['upload']} Analyse your data</a></p>
</div>"""
        return render("About", body, index=True, description="How SWA Data Analysis works: verified pivot tables, charts and programme indicators from CSV, Excel and KoboToolbox data.")

    @app.get("/privacy")
    def privacy():
        ai_text = ("Questions are answered by this site's own calculation engine. If a question cannot be understood by the built-in rules, an AI model (Anthropic's Claude) may be asked to translate it into a calculation. "
                   "Only the question text, column names and summary statistics are sent - never your rows or columns that contain personal data. The numbers themselves are always calculated and double-checked here."
                   if ai_enabled() else "Questions are answered entirely by this site's own calculation engine. Nothing you type or upload is sent to an AI service.")
        keep = (f"Results are deleted automatically {retention} hours after they are created." if retention
                else "Results stay on the server until the site owner deletes them.")
        body = f"""<section class='pagehead'><h1>Privacy</h1><p>What happens to your data on this site.</p></section>
<div class='panel prose'>
<h2>Your uploaded file</h2><p>The file you upload is analysed and then deleted from the server immediately. It is not kept.</p>
<h2>Your results</h2><p>The reports created from your file (Excel, HTML, cleaned data, rejected rows and an audit record) are stored on the server so you can download them.
{escape(keep)} They are only available in the browser that created them: other visitors cannot open them.</p>
<h2>Personal data in your file</h2><p>If your file contains names, phone numbers or ID numbers, the analysis can hash, mask or remove them in every output,
depending on how the analysis profile is configured. Do not upload personal data unless you are allowed to share it and understand how the profile treats it.</p>
<h2>Cookies</h2><p>The site uses one small functional cookie (<code>swa_sid</code>) so your results stay private to your browser. The site owner has a separate sign-in cookie for the admin area.
There are no tracking or analytics cookies set by this site.</p>
<h2>Questions you type</h2><p>{ai_text}</p>
<h2>Advertising</h2><p>Some pages show advertisements. Image and text ads link to the advertiser's site. Embedded ad code runs in an isolated frame that cannot read your uploads, results or cookies,
but, as with any website, an advertiser whose content loads in your browser can see your IP address and browser details.</p>
<h2>Logs</h2><p>Like most hosting services, the host may keep technical logs (such as IP addresses and requested pages) for security and operation.</p>
<h2>Contact</h2><p>Questions or requests about your data: contact the site owner through <a href='{SITE_UTM}' target='_blank' rel='noopener'>swasamson.site</a>.</p>
</div>"""
        return render("Privacy", body, index=True, description="How SWA Data Analysis handles your uploaded files, results, cookies and advertising.")



    # ------------------------------------------------------------------ analyse
    @app.post("/analyse")
    def analyse():
        if not csrf_ok():
            return message("Please try again", "Your session expired. Go back, reload the page and upload again.", 400)
        up = request.files.get("file")
        if not up or not up.filename:
            return redirect("/")
        suffix = Path(up.filename).suffix.lower()
        if suffix not in ALLOWED:
            return message(f"Unsupported file type '{suffix}'", "Please upload a .csv, .xlsx or .json file.", 400)
        stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", Path(up.filename).stem)[:60] or "upload"
        key = request.form.get("profile", "standard")
        profs = profiles()
        if key == "smart":
            title = re.sub(r"[\x00-\x1f\x7f]", " ", request.form.get("title", "")).strip()[:100] or pretty(Path(up.filename).stem)[:80] or "Data analysis"
            qs = [re.sub(r"[\x00-\x1f\x7f]", " ", q).strip()[:300] for q in request.form.get("questions", "").splitlines() if q.strip()][:20]
            try:
                salt = cfg()["privacy_salt"]
            except ConfigError:
                salt = os.environ.get("SWA_PRIVACY_SALT", "")
            with _lock, tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / f"{stem}{suffix}"
                up.save(src)
                my_dir().mkdir(parents=True, exist_ok=True)
                res = run_smart(src, my_dir(), title, qs, display_name=Path(up.filename).name[:100], salt=salt)
            return redirect(f"/runs/{res.out_dir.name}")
        path = (profs.get(key) or profs["standard"])[1]
        try:
            c = cfg(path)
        except ConfigError as exc:
            return message("The settings have a problem", str(exc), 400)
        title = re.sub(r"[\x00-\x1f\x7f]", " ", request.form.get("title", "")).strip()[:100]
        c["dataset_name"] = title or pretty(Path(up.filename).stem)[:80] or "Data analysis"
        c["outputs"] = str(my_dir())
        if c["organisation"] in ("Your Organisation Name",):
            c["organisation"] = ""                      # never print the template placeholder on visitors' reports
        with _lock, tempfile.TemporaryDirectory() as tmp:       # the raw upload is deleted as soon as the run ends
            src = Path(tmp) / f"{stem}{suffix}"
            up.save(src)
            my_dir().mkdir(parents=True, exist_ok=True)
            res = process_file(src, c)
        return redirect(f"/runs/{res.out_dir.name}")

    # ------------------------------------------------------------------ results
    @app.get("/runs/<run_id>")
    def show_run(run_id):
        d = run_dir(run_id)
        audit = {}
        if (d / "audit.json").exists():
            audit = json.loads((d / "audit.json").read_text(encoding="utf-8"))
        status = audit.get("status", "UNKNOWN")
        cls = "ok" if status == "PASSED" else "warn" if "WARNINGS" in status else "bad"
        rows = audit.get("rows", {})
        stats = "".join(
            f"<div class='stat reveal'><b data-count='{rows.get(k, 0)}'>{rows.get(k, '-')}</b><span>{lbl}</span></div>"
            for k, lbl in (("read", "rows read"), ("analysed", "rows analysed"), ("quarantined", "rows set aside"), ("blank", "blank rows")))
        msg = ""
        if audit.get("error"):
            msg = (f"<div class='msgbad'><b>Could not process this file.</b> {escape(audit['error'])}<br>"
                   "<span style='color:var(--mut)'>No report was produced, so no unverified numbers exist. "
                   "Check that your column headers match what the analysis expects.</span></div>")
        warns = "".join(f"<li>{escape(str(w))}</li>" for w in audit.get("warnings", [])[:15])
        if warns:
            msg += f"<div class='panel'><h2>Things to know</h2><ul>{warns}</ul></div>"
        failed = [x for x in audit.get("checks", []) if not x.get("passed")]
        if failed:
            msg += ("<div class='msgbad'><b>Do not use these numbers.</b> Checks failed: "
                    + escape(", ".join(x["name"] for x in failed)) + "</div>")
        btns = ""
        for n, lbl in FILES.items():
            if (d / n).exists():
                primary = "" if n == "report.xlsx" else " ghost"
                btns += f"<a class='btn sm{primary}' href='/runs/{run_id}/file/{n}'>{ICON['download']} {lbl}</a>"
        btns += f"<a class='btn sm' href='/runs/{run_id}/zip'>{ICON['download']} Download everything (.zip)</a>"
        view = (f"<div class='reportbox'><iframe src='/runs/{run_id}/view' title='Report' sandbox referrerpolicy='no-referrer'></iframe></div>"
                f"<p class='keep'><a href='/runs/{run_id}/view' target='_blank' rel='noopener'>Open the report in a full page</a></p>") if (d / "report.html").exists() else ""
        keep = f"This result is deleted automatically after {retention} hours." if retention else ""
        smart_html = ""
        state = load_state(d)
        if state and not audit.get("error"):
            smart_html = smart_panels(state, run_id, token(), ai_enabled())
        name = escape(audit.get("dataset_name") or "Your analysis")
        when = escape(str(audit.get("run_time", "")).replace("T", " ")[:16])
        body = f"""<section class='pagehead'><span class='status {cls}'>{escape(status)}</span>
<h1>{name}</h1><p>Analysis ready &middot; {escape(audit.get('source_file', ''))} &middot; {when}</p></section>
<div class='stats'>{stats}</div>{msg}{smart_html}
<div class='panel'><h2>Download your results</h2><div class='dl'>{btns}</div><p class='keep' style='text-align:left'>{escape(keep)}</p></div>
{view}{ads.render('result_below')}
<p style='text-align:center;margin-top:26px'><a class='btn' href='/'>{ICON['upload']} Analyse another file</a></p>"""
        return render("Result", body, active="home", noindex=True)

    asks: dict = {}

    @app.post("/runs/<run_id>/ask")
    def ask_route(run_id):
        d = run_dir(run_id)
        want_json = request.headers.get("X-Requested-With") == "fetch"
        if not csrf_ok():
            return (jsonify(html="<div class='acard no'><p class='ah' style='font-size:1.05rem'>Your session expired. Reload the page.</p></div>"), 400) if want_json \
                else message("Please try again", "Your session expired. Reload the results page.", 400)
        now = time.time()
        hits = [t for t in asks.get(g.sid, []) if now - t < 3600]
        if len(hits) >= 60:
            html = "<div class='acard no'><p class='ah' style='font-size:1.05rem'>You have asked a lot of questions in the last hour. Please wait a little.</p></div>"
            return (jsonify(html=html), 429) if want_json else message("Slow down a little", "Please wait a few minutes and try again.", 429)
        asks[g.sid] = hits + [now]
        q = re.sub(r"[\x00-\x1f\x7f]", " ", request.form.get("q", "")).strip()[:300]
        ctx = load_qa_context(d)
        if ctx is None:
            html = "<div class='acard no'><p class='ah' style='font-size:1.05rem'>Questions are only available for Smart analysis results.</p></div>"
            return (jsonify(html=html), 400) if want_json else message("Not available", "Questions are only available for Smart analysis results.", 400)
        ans = smart_ask(q, ctx).to_dict()
        html = answer_card(ans, smart_meta(load_state(d)))
        if want_json:
            return jsonify(html=html)
        body = (f"<section class='pagehead'><h1>Your answer</h1></section><div class='panel'>{html}</div>"
                f"<p style='text-align:center'><a class='btn' href='/runs/{escape(run_id)}'>Back to results</a></p>")
        return render("Answer", body, noindex=True)

    @app.get("/runs/<run_id>/view")
    def view_report(run_id):
        p = run_dir(run_id) / "report.html"
        if not p.exists():
            abort(404)
        r = send_file(p, mimetype="text/html")
        r.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        return r

    @app.get("/runs/<run_id>/file/<name>")
    def get_file(run_id, name):
        if name not in FILES:
            abort(404)
        p = run_dir(run_id) / name
        if not p.exists():
            abort(404)
        return send_file(p, as_attachment=True, download_name=f"{run_id}_{name}")

    @app.get("/runs/<run_id>/zip")
    def get_zip(run_id):
        d = run_dir(run_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(d.iterdir()):
                if p.is_file():
                    z.write(p, p.name)
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"{run_id}.zip")

    @app.get("/history")
    def history():
        out = my_dir()
        runs = sorted((p for p in out.iterdir() if p.is_dir()), reverse=True) if out.is_dir() else []
        rows = ""
        for p in runs[:100]:
            try:
                st = json.loads((p / "audit.json").read_text(encoding="utf-8")).get("status", "?")
            except (OSError, ValueError):
                st = "?"
            cls = "ok" if st == "PASSED" else "warn" if "WARNINGS" in st else "bad"
            rows += f"<tr><td><a href='/runs/{escape(p.name)}'>{escape(p.name)}</a></td><td class='{cls}'>{escape(st)}</td></tr>"
        note = "Only analyses made in this browser are listed." + (f" They are deleted after {retention} hours." if retention else "")
        body = (f"<section class='pagehead'><h1>Past runs</h1><p>{escape(note)}</p></section>"
                f"<div class='panel'><table class='runs'>{rows or '<tr><td style=color:var(--mut)>Nothing yet - upload a file to get started.</td></tr>'}</table></div>")
        return render("Past runs", body, active="history", noindex=True)

    X = {"render": render, "message": message, "token": token, "csrf_ok": csrf_ok, "admin_ok": admin_ok, "ads": ads,
         "ads_dir": ads_dir, "admin_pw": admin_pw, "secret": secret, "cfg": cfg, "profiles": profiles, "retention": retention,
         "config_path": config_path, "persistent": persistent}
    register_admin(app, X)

    return app


def serve(config_path: str, host: str = "127.0.0.1", port: int = 8080) -> None:
    app = create_app(config_path)
    if host not in ("127.0.0.1", "localhost") and not (os.environ.get("SWA_PASSWORD") or os.environ.get("SWA_ADMIN_PASSWORD")):
        print("NOTE: serving beyond this computer. Visitors cannot see each other's results, and Settings stay locked "
              "until you set SWA_ADMIN_PASSWORD.")
    try:
        from waitress import serve as wserve
        wserve(app, host=host, port=port, threads=4)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
