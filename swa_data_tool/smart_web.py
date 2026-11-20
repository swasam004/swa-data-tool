"""HTML for the smart-analysis parts of the web interface (answers, ask box, what I found)."""
from __future__ import annotations

from html import escape
from types import SimpleNamespace

from .smart_report import table_fmt, text_value


def _meta(state: dict) -> dict:
    return {c["name"]: SimpleNamespace(decimals=c.get("decimals", 0), prefix=c.get("prefix", "")) for c in state.get("columns", [])}


def answer_card(a: dict, meta: dict, idx: int | None = None) -> str:
    q = escape(a.get("question", ""))
    label = f"<span class='qn'>Q{idx}</span> " if idx else ""
    if not a.get("ok"):
        return (f"<div class='acard no'><p class='aq'>{label}{q}</p><p class='ah' style='font-size:1.05rem'>{escape(a.get('headline', ''))}</p>"
                f"<p class='adet'>{escape(a.get('detail', ''))}</p></div>")
    badge = ("<span class='vbadge'>&#10003; calculated twice, both agree</span>" if a.get("verified") else "<span class='vbadge no'>not verified</span>")
    ai = " <span class='vbadge ai'>AI-assisted wording</span>" if a.get("method") == "ai" else ""
    table = ""
    t = a.get("table")
    if t:
        head = "".join(f"<th>{escape(str(c))}</th>" for c in t["columns"])
        body = ""
        for row in t["rows"][:12]:
            cells = ""
            for j, v in enumerate(row):
                tok = table_fmt(t["fmt"][j], None, meta) if j < len(t["fmt"]) else "text"
                num = isinstance(v, (int, float)) and not isinstance(v, bool)
                cells += f"<td class='{'n' if num else ''}'>{escape(text_value(tok, v) if num else ('' if v is None else str(v)))}</td>"
            body += f"<tr>{cells}</tr>"
        more = f"<p class='keep' style='text-align:left'>Showing 12 of {len(t['rows'])} rows. The full table is in the Excel report.</p>" if len(t["rows"]) > 12 else ""
        table = f"<div class='wrapx'><table class='mini'><tr>{head}</tr>{body}</table></div>{more}"
    notes = "".join(f"<p class='an'>{escape(n)}</p>" for n in a.get("notes", []))
    interp = f"<p class='ai-read'>How I read it: {escape(a['interpretation'])}</p>" if a.get("interpretation") else ""
    return (f"<div class='acard'><p class='aq'>{label}{q}</p><p class='ah'>{escape(a.get('headline', ''))}</p>"
            f"<p class='adet'>{escape(a.get('detail', ''))}</p>{interp}{notes}<p>{badge}{ai}</p>{table}</div>")


def example_questions(state: dict) -> list:
    cols = [c for c in state.get("columns", []) if not c.get("pii")]
    cats = [c["name"] for c in cols if c["role"] in ("category", "boolean")]
    meas = [c["name"] for c in cols if c["role"] == "measure"]
    ex = []
    if cats:
        ex.append(f"What is the most popular {cats[0]}?")
        ex.append(f"What is the least popular {cats[0]}?")
    if meas and cats:
        ex.append(f"What is the average {meas[0]} per {cats[-1]}?")
    if len(meas) >= 2:
        ex.append(f"Is {meas[0]} related to {meas[1]}?")
    if meas:
        ex.append(f"What is the highest {meas[0]}?")
    noun = state.get("noun", "rows")
    ex.append(f"How many {noun} are there?")
    return ex[:6]


def smart_panels(state: dict, run_id: str, csrf: str, ai_on: bool) -> str:
    meta = _meta(state)
    answers = state.get("answers", [])
    parts = []
    if answers:
        parts.append("<div class='panel'><h2>Answers</h2><div id='answers'>"
                     + "".join(answer_card(a, meta, i) for i, a in enumerate(answers, start=1)) + "</div></div>")
    chips = "".join(f"<button type='button' class='qchip'>{escape(q)}</button>" for q in example_questions(state))
    ai_note = ("<p class='keep' style='text-align:left'>AI help is on: if the built-in rules cannot understand a question, an AI model may translate it. "
               "It sees only column names and summary statistics, never your rows. All numbers are still calculated and double-checked by this tool.</p>") if ai_on else ""
    parts.append(f"""<div class='panel askpanel'><h2>Ask your data a question</h2>
<form id='askform' method='post' action='/runs/{escape(run_id)}/ask' data-url='/runs/{escape(run_id)}/ask'>
<input type='hidden' name='csrf' value='{escape(csrf)}'>
<div class='askrow'><input class='t askin' name='q' maxlength='300' placeholder='e.g. What is the average price per owner?' autocomplete='off' aria-label='Your question' required>
<button class='btn'>Ask</button></div></form>
<div class='chips'>{chips}</div>{ai_note}
<div id='askout' aria-live='polite'></div></div>""")
    found = [escape(n) for n in state.get("notes", [])]
    s = state.get("source", {})
    if s:
        found.insert(0, escape(f"Analysed table: {('sheet ' + repr(s.get('sheet')) + ', range ' + s.get('ref', '') + ' - ') if s.get('kind') == 'excel' else ''}{s.get('rows', 0):,} rows x {s.get('cols', 0)} columns."))
    for lk in state.get("lookups", []):
        found.append(escape(f"Looked up '{lk['value']}' from sheet '{lk['sheet']}' using '{lk['key']}' ({lk['matched']} matched)."))
    ins = state.get("insights", [])
    parts.append("<div class='panel'><h2>What I found in your file</h2><ul>" + "".join(f"<li>{f}</li>" for f in found) + "</ul>"
                 + (("<h2 style='margin-top:22px'>Key insights</h2><ul>" + "".join(f"<li>{escape(i)}</li>" for i in ins[:8]) + "</ul>"
                     "<p class='keep' style='text-align:left'>Patterns in this file only - they do not prove causes. The full analysis is in the Excel report.</p>") if ins else "") + "</div>")
    return "".join(parts)


SMART_CSS = """
.acard{background:rgba(255,255,255,.05);border:1px solid var(--line);border-radius:18px;padding:18px 20px;margin:14px 0}
.acard.no{border-color:rgba(251,191,36,.5)}
.aq{margin:0 0 6px;color:#9db0d6;font-weight:700;font-size:15px}.qn{display:inline-block;background:linear-gradient(135deg,#22d3ee,#7c4dff);color:#fff;border-radius:99px;padding:1px 10px;margin-right:6px;font-size:13px}
.ah{margin:2px 0;font-size:clamp(1.5rem,3.4vw,2.2rem);font-weight:800;letter-spacing:-.02em;line-height:1.15;color:#fff}.adet{margin:4px 0 8px;color:#cbd8f6}
.ai-read{margin:6px 0;color:#9db0d6;font-size:14px}.an{margin:6px 0;color:#ffe29a;font-size:14px}
.vbadge{display:inline-block;font-size:12.5px;font-weight:800;color:var(--ok);border:1px solid var(--ok);border-radius:99px;padding:2px 12px;margin-right:6px}.vbadge.no{color:var(--bad);border-color:var(--bad)}.vbadge.ai{color:var(--cyan);border-color:var(--cyan)}
.wrapx{overflow-x:auto}table.mini{border-collapse:collapse;font-size:14px;margin-top:6px}.mini th{background:rgba(255,255,255,.1);text-align:left;padding:6px 12px}.mini td{padding:6px 12px;border-bottom:1px solid var(--line)}.mini td.n{text-align:right}
.askrow{display:flex;gap:10px;flex-wrap:wrap}.askin{flex:1;min-width:240px;background:rgba(2,6,20,.65);color:#eaf0ff;border:1px solid var(--line);border-radius:99px;padding:13px 20px;font:500 16px var(--font)}
.askin:focus{outline:2px solid rgba(34,211,238,.6);border-color:transparent}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.qchip{background:var(--glass);color:#cfdcff;border:1px solid var(--line);border-radius:99px;padding:7px 14px;font:600 13.5px var(--font);cursor:pointer;transition:.2s}.qchip:hover{border-color:var(--cyan);color:#fff}
.askbox{margin-top:12px;color:#34406b}.askbox summary{cursor:pointer;font-weight:700;font-size:15px;color:#5b49d6;list-style:none}.askbox summary::-webkit-details-marker{display:none}
.askbox textarea{width:100%;margin-top:8px;border:1.5px solid #d6dbee;border-radius:14px;padding:10px 14px;font:500 15px var(--font);color:#1b2446;resize:vertical;min-height:84px}
.askbox textarea:focus{outline:0;border-color:#7c4dff}
.spin{display:inline-block;width:18px;height:18px;border:3px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:rot .8s linear infinite;vertical-align:middle}@keyframes rot{to{transform:rotate(360deg)}}
"""
