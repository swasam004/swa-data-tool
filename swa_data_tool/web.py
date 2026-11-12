"""Web interface: upload a data file in the browser, get the verified report back.

    swa-tool serve -c config.yaml            (then open http://127.0.0.1:8080)

Set SWA_PASSWORD to require a password (HTTP basic auth, any user name).
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import tempfile
import threading
import zipfile
from html import escape
from pathlib import Path

from flask import Flask, Response, abort, redirect, request, send_file

from .config import ConfigError, load_config
from .pipeline import process_file

ALLOWED = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".json"}
RUN_ID = re.compile(r"^[A-Za-z0-9_\-]{1,200}$")
FILES = {"report.xlsx": "Excel report", "report.html": "HTML report", "clean_data.csv": "Clean data (CSV)",
         "quarantined_rows.csv": "Rejected rows (CSV)", "audit.json": "Audit record", "run.log": "Run log"}
PLACEHOLDER_SALT = "replace-this-with-a-long-secret-phrase"
_lock = threading.Lock()          # one analysis at a time (the pipeline logs to one shared logger)

CSS = """:root{--bg:#f6f7f9;--fg:#1c2330;--card:#fff;--mut:#667085;--line:#e3e6ec;--pri:#1f6feb;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e}
@media(prefers-color-scheme:dark){:root{--bg:#12161d;--fg:#e6e9ef;--card:#1a2029;--mut:#98a2b3;--line:#2a3240;--pri:#4c8dff;--ok:#3fb950;--warn:#d29922;--bad:#f85149}}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;gap:20px;align-items:center;padding:14px 24px;border-bottom:1px solid var(--line);background:var(--card)}
header b{font-size:18px;margin-right:auto}header a{color:var(--fg);text-decoration:none;color:var(--mut)}header a:hover{color:var(--pri)}
main{max-width:820px;margin:28px auto;padding:0 18px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:22px;margin-bottom:18px}
h1{font-size:24px;margin:0 0 6px}h2{font-size:17px;margin:0 0 10px}.mut{color:var(--mut);font-size:14px}
#drop{border:2px dashed var(--line);border-radius:12px;padding:44px 16px;text-align:center;cursor:pointer;transition:.15s}
#drop.over,#drop:hover{border-color:var(--pri);background:color-mix(in srgb,var(--pri) 6%,transparent)}
button,.btn{background:var(--pri);color:#fff;border:0;border-radius:8px;padding:10px 18px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
.btn.sec{background:transparent;color:var(--pri);border:1px solid var(--line)}button:disabled{opacity:.5}
.tag{display:inline-block;padding:3px 12px;border-radius:99px;font-weight:600;font-size:14px;border:1px solid currentColor}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}.stats div{flex:1;min-width:110px;border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.stats b{display:block;font-size:24px}.dl{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
textarea{width:100%;height:60vh;font:13px/1.4 ui-monospace,monospace;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:10px}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);font-size:14px}
.banner{border:1px solid var(--warn);color:var(--warn);border-radius:10px;padding:10px 14px;margin-bottom:18px}
iframe{width:100%;height:70vh;border:1px solid var(--line);border-radius:8px;background:#fff}"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)} - SWA Data Analysis</title><style>{CSS}</style></head><body>"
            f"<header><b>SWA Data Analysis</b><a href='/'>Analyse</a><a href='/history'>Past runs</a><a href='/settings'>Settings</a></header>"
            f"<main>{body}</main></body></html>")


def create_app(config_path: str) -> Flask:
    config_path = str(Path(config_path).resolve())
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("SWA_MAX_MB", "50")) * 1024 * 1024
    password = os.environ.get("SWA_PASSWORD", "")

    def cfg():
        return load_config(config_path)

    def outputs() -> Path:
        return Path(cfg()["outputs"])

    @app.before_request
    def guard():
        if password:
            a = request.authorization
            if not a or not secrets.compare_digest(a.password or "", password):
                return Response("Password required.", 401, {"WWW-Authenticate": 'Basic realm="SWA"'})

    @app.after_request
    def headers(r):
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["Cache-Control"] = "no-store"
        return r

    def run_dir(run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            abort(404)
        d = outputs() / run_id
        if not d.is_dir():
            abort(404)
        return d

    @app.get("/")
    def home():
        try:
            c = cfg()
        except ConfigError as exc:
            return page("Error", f"<div class='card'><h2 class='bad'>The saved settings have a problem</h2><p>{escape(str(exc))}</p>"
                                 "<a class='btn' href='/settings'>Fix settings</a></div>")
        banner = ("<div class='banner'>The privacy salt is still the placeholder. Open <a href='/settings'>Settings</a> and set a long secret "
                  "phrase before using real data.</div>") if c["privacy_salt"] in ("", PLACEHOLDER_SALT) else ""
        cols = ", ".join(escape(str(x["name"])) for x in c["columns"])
        return page("Analyse", f"""{banner}<div class='card'><h1>{escape(c['dataset_name'])}</h1>
<p class='mut'>Drop your data file here. You get a verified Excel report, an HTML report, clean data and a list of rejected rows.</p>
<form id='f' method='post' action='/analyse' enctype='multipart/form-data'>
<label id='drop' for='file'><b>Choose a file</b> or drag it here<br><span class='mut'>CSV, Excel or JSON - up to {app.config['MAX_CONTENT_LENGTH'] // 1048576} MB</span><br><span id='name' class='mut'></span></label>
<input id='file' name='file' type='file' accept='.csv,.tsv,.txt,.xlsx,.xlsm,.xls,.json' hidden required>
<p><button id='go' disabled>Analyse</button> <span id='busy' class='mut' hidden>Analysing - this can take a minute for big files...</span></p></form>
<p class='mut'>Columns expected: {cols}</p></div>
<script>const f=document.getElementById('file'),d=document.getElementById('drop'),g=document.getElementById('go');
function show(){{if(f.files[0]){{document.getElementById('name').textContent=f.files[0].name;g.disabled=false}}}}
f.onchange=show;['dragenter','dragover'].forEach(e=>d.addEventListener(e,x=>{{x.preventDefault();d.classList.add('over')}}));
['dragleave','drop'].forEach(e=>d.addEventListener(e,x=>{{x.preventDefault();d.classList.remove('over')}}));
d.addEventListener('drop',x=>{{f.files=x.dataTransfer.files;show()}});
document.getElementById('f').onsubmit=()=>{{g.disabled=true;document.getElementById('busy').hidden=false}};</script>""")

    @app.post("/analyse")
    def analyse():
        up = request.files.get("file")
        if not up or not up.filename:
            return redirect("/")
        suffix = Path(up.filename).suffix.lower()
        if suffix not in ALLOWED:
            return page("Error", f"<div class='card'><h2 class='bad'>Unsupported file type '{escape(suffix)}'</h2>"
                                 "<p>Please upload a .csv, .xlsx or .json file.</p><a class='btn' href='/'>Back</a></div>"), 400
        stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", Path(up.filename).stem)[:60] or "upload"
        try:
            c = cfg()
        except ConfigError as exc:
            return page("Error", f"<div class='card'><p class='bad'>{escape(str(exc))}</p></div>"), 400
        with _lock, tempfile.TemporaryDirectory() as tmp:       # the raw upload is deleted as soon as the run ends
            src = Path(tmp) / f"{stem}{suffix}"
            up.save(src)
            Path(c["outputs"]).mkdir(parents=True, exist_ok=True)
            res = process_file(src, c)
        return redirect(f"/runs/{res.out_dir.name}")

    @app.get("/runs/<run_id>")
    def show_run(run_id):
        d = run_dir(run_id)
        audit = {}
        if (d / "audit.json").exists():
            audit = json.loads((d / "audit.json").read_text(encoding="utf-8"))
        status = audit.get("status", "UNKNOWN")
        cls = "ok" if status == "PASSED" else "warn" if "WARNINGS" in status else "bad"
        rows = audit.get("rows", {})
        stats = "".join(f"<div><b>{rows.get(k, '-')}</b><span class='mut'>{lbl}</span></div>"
                        for k, lbl in (("read", "rows read"), ("analysed", "analysed"), ("quarantined", "rejected"), ("blank", "blank")))
        msg = ""
        if audit.get("error"):
            msg = f"<p class='bad'><b>Could not process this file.</b> {escape(audit['error'])}</p><p class='mut'>No report was produced, so no unverified numbers exist. Check that the column names in Settings match your file's headers.</p>"
        warns = "".join(f"<li>{escape(str(w))}</li>" for w in audit.get("warnings", [])[:15])
        if warns:
            msg += f"<h2>Warnings</h2><ul>{warns}</ul>"
        failed = [x for x in audit.get("checks", []) if not x.get("passed")]
        if failed:
            msg += "<p class='bad'><b>Do not use these numbers.</b> Checks failed: " + escape(", ".join(x["name"] for x in failed)) + "</p>"
        links = "".join(f"<a class='btn sec' href='/runs/{run_id}/file/{n}'>{lbl}</a>" for n, lbl in FILES.items() if (d / n).exists())
        links += f"<a class='btn' href='/runs/{run_id}/zip'>Download everything (.zip)</a>"
        view = f"<div class='card'><iframe src='/runs/{run_id}/view' title='Report'></iframe></div>" if (d / "report.html").exists() else ""
        return page("Result", f"""<div class='card'><h1>Result <span class='tag {cls}'>{escape(status)}</span></h1>
<p class='mut'>{escape(audit.get('source_file', ''))} - {escape(audit.get('run_time', ''))}</p><div class='stats'>{stats}</div>{msg}
<div class='dl'>{links}</div></div>{view}""")

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
        out = outputs()
        runs = sorted((p for p in out.iterdir() if p.is_dir()), reverse=True) if out.is_dir() else []
        rows = ""
        for p in runs[:100]:
            try:
                st = json.loads((p / "audit.json").read_text(encoding="utf-8")).get("status", "?")
            except (OSError, ValueError):
                st = "?"
            cls = "ok" if st == "PASSED" else "warn" if "WARNINGS" in st else "bad"
            rows += f"<tr><td><a href='/runs/{escape(p.name)}'>{escape(p.name)}</a></td><td class='{cls}'>{escape(st)}</td></tr>"
        return page("Past runs", f"<div class='card'><h1>Past runs</h1><table>{rows or '<tr><td class=mut>Nothing yet.</td></tr>'}</table></div>")

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        note = ""
        text = Path(config_path).read_text(encoding="utf-8-sig")
        if request.method == "POST":
            text = request.form.get("text", "")
            fd, tmp = tempfile.mkstemp(suffix=".yaml", dir=str(Path(config_path).parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                c = load_config(tmp)                       # validates exactly like `swa-tool check-config`
                if request.form.get("action") == "save":
                    Path(config_path + ".bak").write_text(Path(config_path).read_text(encoding="utf-8-sig"), encoding="utf-8")
                    os.replace(tmp, config_path)
                    os.chmod(config_path, 0o600)
                    note = "<p class='ok'><b>Saved.</b> New settings apply to the next analysis (previous version kept as config.yaml.bak).</p>"
                else:
                    note = f"<p class='ok'><b>Settings are valid:</b> {len(c['columns'])} column(s), {len(c['pivots'])} pivot(s).</p>"
            except ConfigError as exc:
                note = f"<p class='bad'><b>Problem:</b> {escape(str(exc))}</p>"
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        return page("Settings", f"""<div class='card'><h1>Settings</h1><p class='mut'>This is your config.yaml. Each <code>source:</code> must match a column header in your data file exactly.</p>{note}
<form method='post'><textarea name='text' spellcheck='false'>{escape(text)}</textarea><p><button name='action' value='check'>Check</button>
<button name='action' value='save'>Save</button></p></form></div>""")

    return app


def serve(config_path: str, host: str = "127.0.0.1", port: int = 8080) -> None:
    app = create_app(config_path)
    if host not in ("127.0.0.1", "localhost") and not os.environ.get("SWA_PASSWORD"):
        print("WARNING: serving beyond this computer WITHOUT a password. Set SWA_PASSWORD first.")
    try:
        from waitress import serve as wserve
        wserve(app, host=host, port=port, threads=4)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
