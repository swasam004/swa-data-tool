"""Turns a smart analysis into extra report sheets (Excel) and sections (HTML)."""
from __future__ import annotations

import base64
import io
import math
from html import escape

from .checks import Check
from .smart_analyze import Analysis, fmt_num
from .smart_clean import Dataset
from .smart_qa import Answer, QAContext


# ------------------------------------------------------------------------------------ number formats
def table_fmt(token: str, ctx: QAContext | None, cols_meta: dict | None = None) -> str:
    """Resolve a table format token to a concrete one: text | int | pct | r | dec:<n>:<prefix>"""
    if token.startswith("num:"):
        name = token[4:]
        c = (ctx.cols.get(name) if ctx else None) or (cols_meta or {}).get(name)
        if c is None:
            return "dec:2:"
        dec = max(getattr(c, "decimals", 0), 0)
        return f"dec:{dec}:{getattr(c, 'prefix', '')}"
    return token


def excel_numfmt(token: str) -> str:
    if token == "int":
        return "#,##0"
    if token == "pct":
        return "0.0%"
    if token == "r":
        return "0.00"
    if token.startswith("dec:"):
        _, dec, prefix = (token.split(":", 2) + [""])[:3]
        d = int(dec)
        body = "#,##0" + ("." + "0" * d if d else "")
        return (f'"{prefix}"' + body) if prefix else body
    return "General"


def text_value(token: str, v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if token == "int":
        return f"{int(round(v)):,}"
    if token == "pct":
        return f"{v * 100:.1f}%"
    if token == "r":
        return f"{v:.2f}"
    if token.startswith("dec:"):
        _, dec, prefix = (token.split(":", 2) + [""])[:3]
        return f"{prefix}{v:,.{int(dec)}f}"
    return str(v)


# ------------------------------------------------------------------------------------ building
def _table(heading, columns, rows, fmt, chart=None, colorscale=None, note=None):
    return {"type": "table", "heading": heading, "columns": columns, "rows": rows, "fmt": fmt, "chart": chart, "colorscale": colorscale, "note": note}


def _text(*lines):
    return {"type": "text", "lines": [l for l in lines if l]}


def _heading(t):
    return {"type": "heading", "text": t}


def build_extras(ds: Dataset, A: Analysis, answers: list, ctx: QAContext, scan) -> dict:
    cm = ctx.cols
    front, back, checks, warnings = [], [], list(A.checks), []

    # ---------------- Answers
    if answers:
        blocks = []
        for i, a in enumerate(answers, start=1):
            blocks.append(_heading(f"Q{i}. {a.question}"))
            if a.ok:
                lines = [f"Answer: {a.headline}", a.detail, f"How I read your question: {a.interpretation}" if a.interpretation else "",
                         "Checked: calculated twice by independent methods and both results agree." if a.verified else "Not checked."]
                lines += [f"Note: {n}" for n in a.notes]
                blocks.append(_text(*lines))
                if a.table:
                    fm = [table_fmt(t, ctx) for t in a.table["fmt"]]
                    blocks.append(_table("", a.table["columns"], a.table["rows"], fm, chart=a.chart))
                checks.append(Check(f"Answer to: {a.question[:80]}", "two methods agree", "agree" if a.verified else "DIFFER", a.verified))
            else:
                blocks.append(_text(a.headline, a.detail))
                warnings.append(f"A question could not be answered: {a.question[:90]}")
        front.append({"name": "Answers", "title": "Answers to your questions", "blocks": blocks})

    # ---------------- What I found + insights
    s = ds.source
    found = [f"Analysed table: sheet '{s['sheet']}', range {s['ref']} - {s['rows']:,} rows x {s['cols']} columns." if s.get("kind") == "excel"
             else f"Analysed table: {s['rows']:,} rows x {s['cols']} columns."]
    if len(s.get("stacked", [])) > 1:
        found.append("Several tables with the same columns were stacked: " + ", ".join(s["stacked"]))
    for lk in ds.lookups:
        found.append(f"Looked up '{lk['value']}' from sheet '{lk['sheet']}' using '{lk['key']}': {lk['matched']} rows matched" + (f", {len(lk['unmatched'])} value(s) not found." if lk["unmatched"] else "."))
    found += ds.notes
    ib = [_heading("What I found in your file"), _text(*found)]
    if A.insights:
        ib += [_heading("Key insights"), _text(*[f"{i}. {t}" for i, t in enumerate(A.insights, start=1)])]
    ib.append(_text("These insights are patterns in this file only. They do not prove causes and are only as representative as the data you uploaded."))
    front.append({"name": "Insights", "title": "What the data shows", "blocks": ib})

    # ---------------- Profile
    prof = [_table("Every column", ["Column", "Type", "Values", "Missing", "% missing", "Different values", "Summary"], A.profile,
                   ["text", "text", "int", "int", "pct", "int", "text"])]
    if A.stats:
        rows = []
        for name, st in A.stats.items():
            c = cm[name]
            rows.append([name, st["n"], st["mean"], st["median"], st["std"], st["min"], st["q1"], st["q3"], st["max"], st["sum"]])
        prof.append(_table("Number columns", ["Column", "Values", "Mean", "Median", "Std dev", "Min", "25th pct", "75th pct", "Max", "Sum"], rows,
                           ["text", "int"] + ["dec:2:"] * 8))
    back.append({"name": "Column Profile", "title": "Column profile", "blocks": prof})

    # ---------------- Frequencies
    fb = []
    for col, rows in list(A.frequencies.items())[:12]:
        chart = {"type": "bar" if len(rows) > 6 else "column", "title": f"Rows by {col}", "x": 0, "y": [1]} if len(rows) <= 25 else None
        fb.append(_table(col, [col, "Rows", "% of rows"], [[k, v, p] for k, v, p in rows], ["text", "int", "pct"], chart=chart))
    if fb:
        back.append({"name": "Frequencies", "title": "How often each value appears", "blocks": fb})

    # ---------------- Group comparisons
    gb = []
    for g in A.groups:
        m = cm[g["measure"]]
        dec = f"dec:{max(m.decimals, 2)}:{m.prefix}"
        d0 = f"dec:{m.decimals}:{m.prefix}"
        rows = [[r["level"], r["n"], r["mean"], r["median"], r["min"], r["max"], r["sum"]] for r in g["rows"]]
        gb.append(_table(f"{g['measure']} by {g['by']}", [g["by"], "Rows", "Mean", "Median", "Min", "Max", "Sum"], rows,
                         ["text", "int", dec, dec, d0, d0, d0], chart={"type": "bar" if len(rows) > 6 else "column", "title": f"Average {g['measure']} by {g['by']}", "x": 0, "y": [2]},
                         note="Groups with fewer than 5 rows are not very reliable." if any(r["n"] < 5 for r in g["rows"]) else None))
    if gb:
        back.append({"name": "Compare Groups", "title": "Comparing groups", "blocks": gb})

    # ---------------- Correlations
    if A.correlations:
        rows = [[d["a"], d["b"], d["r"], d["n"], f"{d['label']} {'positive' if d['r'] > 0 else 'negative'}", "yes" if d["reliable"] else "no (under 10 rows)"] for d in A.correlations]
        names = sorted({d["a"] for d in A.correlations} | {d["b"] for d in A.correlations})
        mat = []
        look = {}
        for d in A.correlations:
            look[(d["a"], d["b"])] = d["r"]
            look[(d["b"], d["a"])] = d["r"]
        for a in names:
            mat.append([a] + [1.0 if a == b else look.get((a, b)) for b in names])
        back.append({"name": "Correlations", "title": "Which numbers move together", "blocks": [
            _text("A correlation (r) goes from -1 to +1. Near 0 means no straight-line relationship. It shows a pattern, not that one thing causes another."),
            _table("Pairs (strongest first)", ["Column A", "Column B", "Correlation (r)", "Rows used", "Strength", "Reliable?"], rows, ["text", "text", "r", "int", "text", "text"]),
            _table("Correlation matrix", [""] + names, mat, ["text"] + ["r"] * len(names), colorscale="r")]})

    # ---------------- Trends
    tb = []
    for t in A.trends:
        if t.get("monthly"):
            tb.append(_table(f"{t['by']} - rows per month", [t["by"], "Rows"], [[p, c] for p, c in zip(t["periods"], t["counts"])], ["text", "int"],
                             chart={"type": "line", "title": f"Rows per month ({t['by']})", "x": 0, "y": [1]} if len(t["periods"]) <= 60 else None))
            continue
        ms = list(t["measures"])
        cols = [t["by"], "Rows"] + [f"Average {m}" for m in ms]
        rows = []
        for i, p in enumerate(t["periods"]):
            rows.append([str(p), t["counts"][i]] + [t["measures"][m]["means"].get(p) for m in ms])
        fm = ["text", "int"] + [f"dec:{max(cm[m].decimals, 2)}:{cm[m].prefix}" for m in ms]
        tb.append(_table(f"{t['by']}", cols, rows, fm, chart={"type": "line", "title": f"Average {ms[0]} by {t['by']}" if ms else f"Rows by {t['by']}", "x": 0, "y": [2 if ms else 1]}))
    if tb:
        back.append({"name": "Trends", "title": "Change over time", "blocks": tb})

    # ---------------- Outliers
    if A.outliers:
        rows = [[o["column"], o["sheet_row"], o["value"], o["low"], o["high"], o["side"]] for o in A.outliers[:200]]
        back.append({"name": "Outliers", "title": "Unusual values (kept in the analysis)", "blocks": [
            _text("A value is flagged when it is more than 1.5 x the interquartile range beyond the middle half of the data. Flagged does not mean wrong - check them."),
            _table("", ["Column", "Row in your sheet", "Value", "Normal range from", "Normal range to", "Side"], rows, ["text", "int", "dec:2:", "dec:2:", "dec:2:", "text"])]})

    # ---------------- Cleaning log
    if ds.log:
        back.append({"name": "Cleaning Log", "title": "Every change made to your data", "blocks": [
            _text("Nothing is changed silently. Each line below says what was done and where."),
            _table("", ["Type", "Column", "What I did"], [[l["kind"], l["column"], l["message"]] for l in ds.log], ["text", "text", "text"])]})
    return {"front": front, "back": back, "checks": checks, "warnings": warnings,
            "audit": {"source": ds.source, "questions": len(answers), "answers_verified": sum(1 for a in answers if a.ok and a.verified),
                      "insights": len(A.insights), "cleaning_steps": len(ds.log)}}


# ------------------------------------------------------------------------------------ Excel writer
def write_extra_sheet(wb, ws, spec: dict, fmt) -> None:
    title = fmt(bold=True, font_size=14)
    h2 = fmt(bold=True, font_size=12)
    head = fmt(bold=True, font_color="#FFFFFF", bg_color="#1F3864", border=1, text_wrap=True, valign="vcenter")
    wrap = fmt(text_wrap=True, valign="top")
    note = fmt(italic=True, font_color="#595959")
    text = fmt()
    ws.write_string(0, 0, spec["title"], title)
    ws.set_column(0, 0, 36)
    ws.set_column(1, 12, 17)
    r = 2
    for b in spec["blocks"]:
        if b["type"] == "heading":
            ws.write_string(r, 0, b["text"], h2)
            r += 1
        elif b["type"] == "text":
            for line in b["lines"]:
                ws.merge_range(r, 0, r, 8, line, wrap)
                ws.set_row(r, 15 * max(1, math.ceil(len(line) / 125)))
                r += 1
            r += 1
        else:
            if b.get("heading"):
                ws.write_string(r, 0, b["heading"], h2)
                r += 1
            hdr = r
            for j, c in enumerate(b["columns"]):
                ws.write_string(hdr, j, str(c), head)
            ws.set_row(hdr, 30)
            for i, row in enumerate(b["rows"], start=1):
                for j, v in enumerate(row):
                    token = b["fmt"][j] if j < len(b["fmt"]) else "text"
                    if v is None or (isinstance(v, float) and math.isnan(v)):
                        continue
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        ws.write_number(hdr + i, j, float(v), fmt(num_format=excel_numfmt(token)))
                    else:
                        ws.write_string(hdr + i, j, str(v), wrap if token == "text" and len(str(v)) > 40 else text)
            last = hdr + len(b["rows"])
            if b.get("colorscale") and b["rows"]:
                ws.conditional_format(hdr + 1, 1, last, len(b["columns"]) - 1, {"type": "3_color_scale", "min_color": "#F8696B", "mid_color": "#FFFFFF", "max_color": "#63BE7B",
                                                                                 "min_type": "num", "min_value": -1, "mid_type": "num", "mid_value": 0, "max_type": "num", "max_value": 1})
            span = len(b["rows"]) + 2
            ch = b.get("chart")
            if ch and 2 <= len(b["rows"]) <= 40:
                chart = wb.add_chart({"type": "bar" if ch["type"] == "bar" else "line" if ch["type"] == "line" else "column"})
                for y in ch["y"]:
                    chart.add_series({"name": [ws.get_name(), hdr, y], "categories": [ws.get_name(), hdr + 1, ch["x"], last, ch["x"]],
                                      "values": [ws.get_name(), hdr + 1, y, last, y]})
                chart.set_title({"name": ch["title"], "name_font": {"size": 11}})
                chart.set_legend({"none": True})
                chart.set_size({"width": 470, "height": 270})
                ws.insert_chart(hdr, len(b["columns"]) + 1, chart)
                span = max(span, 15)
            if b.get("note"):
                ws.write_string(last + 1, 0, b["note"], note)
                span += 1
            r = hdr + span + 1
    ws.freeze_panes(1, 0)


# ------------------------------------------------------------------------------------ HTML writer
def _png(b) -> str | None:
    ch = b.get("chart")
    rows = b["rows"]
    if not ch or not (2 <= len(rows) <= 40):
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = [str(r[ch["x"]]) for r in rows]
    vals = [(r[ch["y"][0]] or 0) for r in rows]
    fig, ax = plt.subplots(figsize=(7.5, 3.6), dpi=100)
    try:
        if ch["type"] == "line":
            ax.plot(range(len(vals)), vals, marker="o", color="#3b7bff")
            ax.set_xticks(range(len(vals)))
            ax.set_xticklabels(labels, rotation=45 if len(labels) > 8 else 0, ha="right" if len(labels) > 8 else "center", fontsize=8)
        elif ch["type"] == "bar":
            ax.barh(range(len(vals)), vals, color="#3b7bff")
            ax.set_yticks(range(len(vals)))
            ax.set_yticklabels(labels, fontsize=8)
            ax.invert_yaxis()
        else:
            ax.bar(range(len(vals)), vals, color="#3b7bff")
            ax.set_xticks(range(len(vals)))
            ax.set_xticklabels(labels, rotation=45 if len(labels) > 6 else 0, ha="right" if len(labels) > 6 else "center", fontsize=8)
        ax.set_title(ch["title"], fontsize=10)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
    finally:
        plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def html_sections(extras: dict, names=("Answers", "Insights", "Compare Groups", "Correlations", "Trends", "Cleaning Log")) -> str:
    out = []
    for spec in list(extras.get("front", [])) + list(extras.get("back", [])):
        if spec["name"] not in names:
            continue
        out.append(f"<h2>{escape(spec['title'])}</h2>")
        for b in spec["blocks"]:
            if b["type"] == "heading":
                out.append(f"<h3>{escape(b['text'])}</h3>")
            elif b["type"] == "text":
                out.append("".join(f"<p>{escape(l)}</p>" for l in b["lines"]))
            else:
                if b.get("heading"):
                    out.append(f"<p><b>{escape(b['heading'])}</b></p>")
                head = "".join(f"<th>{escape(str(c))}</th>" for c in b["columns"])
                body = ""
                for row in b["rows"][:60]:
                    cells = ""
                    for j, v in enumerate(row):
                        token = b["fmt"][j] if j < len(b["fmt"]) else "text"
                        num = isinstance(v, (int, float)) and not isinstance(v, bool)
                        cells += f"<td class='{'n' if num else ''}'>{escape(text_value(token, v) if num else ('' if v is None else str(v)))}</td>"
                    body += f"<tr>{cells}</tr>"
                out.append(f"<div class='wrap'><table><tr>{head}</tr>{body}</table></div>")
                png = _png(b)
                if png:
                    out.append(f"<img alt='{escape(b['chart']['title'], quote=True)}' src='data:image/png;base64,{png}'>")
    return "".join(out)
