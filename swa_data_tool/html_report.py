"""Self-contained HTML report (works offline, can be e-mailed as one file)."""
from __future__ import annotations

import base64
import html
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from . import __version__  # noqa: E402
from .indicators import describe  # noqa: E402
from .pivots import PivotResult, percent_matrix  # noqa: E402

MAX_CHART_CATEGORIES = 40
E = html.escape

CSS = """
body{font-family:Arial,Helvetica,sans-serif;margin:24px auto;max-width:1100px;color:#1a1a1a;padding:0 16px}
h1{font-size:22px}h2{font-size:17px;margin-top:34px;border-bottom:2px solid #1F3864;padding-bottom:4px}
table{border-collapse:collapse;margin:10px 0;font-size:13px}th{background:#1F3864;color:#fff;padding:6px 10px;text-align:left}
td{border:1px solid #ccc;padding:5px 10px}td.n{text-align:right}tr.t td{font-weight:bold;border-top:2px solid #333}
.s{display:inline-block;padding:6px 14px;border-radius:4px;font-weight:bold}
.ok{background:#C6EFCE;color:#006100}.warn{background:#FFEB9C;color:#7F6000}.bad{background:#FFC7CE;color:#9C0006}
.muted{color:#595959;font-size:12px}img{max-width:100%;margin:8px 0}.wrap{overflow-x:auto}
"""


def _fmt(v, dec):
    return "" if v is None else f"{v:,.{dec}f}"


def _chart_png(pr: PivotResult):
    spec = pr.spec
    ct = spec["chart"]
    if ct == "none" or not pr.row_labels:
        return None, None
    if len(pr.row_labels) > MAX_CHART_CATEGORIES:
        return None, f"Chart omitted: {len(pr.row_labels)} categories is too many to read."
    if ct == "pie" and any((v or 0) < 0 for row in pr.matrix for v in row):
        return None, "Chart omitted: a pie chart cannot show negative values."
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=110)
    labels = pr.row_labels
    try:
        if ct == "pie":
            ax.pie([row[0] or 0 for row in pr.matrix], labels=labels, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")
        else:
            series = ([(lbl, [row[j] or 0 for row in pr.matrix]) for j, lbl in enumerate(pr.col_labels)]
                      if pr.has_cols else [(pr.label, [row[0] or 0 for row in pr.matrix])])
            x = range(len(labels))
            if ct in ("column", "column_stacked"):
                n = len(series)
                stacked = ct == "column_stacked"
                w = 0.8 if (stacked or n == 1) else 0.8 / n
                bottom = [0.0] * len(labels)
                for k, (name, vals) in enumerate(series):
                    pos = [i if (stacked or n == 1) else i - 0.4 + w * (k + 0.5) for i in x]
                    ax.bar(pos, vals, w, bottom=bottom if stacked else None, label=name)
                    if stacked:
                        bottom = [b + v for b, v in zip(bottom, vals)]
                ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=45 if len(labels) > 6 else 0, ha="right" if len(labels) > 6 else "center")
            elif ct == "bar":
                n = len(series)
                w = 0.8 / n
                for k, (name, vals) in enumerate(series):
                    ax.barh([i - 0.4 + w * (k + 0.5) for i in x], vals, w, label=name)
                ax.set_yticks(list(x)); ax.set_yticklabels(labels); ax.invert_yaxis()
            else:
                for name, vals in series:
                    ax.plot(list(x), vals, marker="o", label=name)
                ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=45 if len(labels) > 6 else 0, ha="right" if len(labels) > 6 else "center")
            ax.grid(axis="y" if ct != "bar" else "x", alpha=0.3)
            ax.set_axisbelow(True)
            (ax.set_xlabel if ct == "bar" else ax.set_ylabel)(pr.label)
            if pr.has_cols or len(series) > 1:
                ax.legend(fontsize=8)
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")) if ct != "bar" else \
                ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.set_title(spec["name"], fontsize=12)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
    finally:
        plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii"), None


def _table(headers, rows, num_from=1):
    out = ["<div class='wrap'><table><tr>" + "".join(f"<th>{E(str(h))}</th>" for h in headers) + "</tr>"]
    for r in rows:
        cls = " class='t'" if r and r[0] == "Total" else ""
        out.append(f"<tr{cls}>" + "".join(
            f"<td class='n'>{E(str(c))}</td>" if i >= num_from else f"<td>{E(str(c))}</td>" for i, c in enumerate(r)) + "</tr>")
    out.append("</table></div>")
    return "".join(out)


def build_html(ctx: dict) -> str:
    cfg, cr, pivots, checks = ctx["cfg"], ctx["cr"], ctx["pivots"], ctx["checks"]
    st = ctx["status"]
    cls = "ok" if st == "PASSED" else "warn" if st == "PASSED WITH WARNINGS" else "bad"
    h = [f"<!doctype html><html><head><meta charset='utf-8'><title>{E(cfg['dataset_name'])} report</title><style>{CSS}</style></head><body>",
         (f"<p class='muted'>{E(cfg['organisation'])}</p>" if cfg["organisation"] else "")
         + f"<h1>{E(cfg['dataset_name'])} - analysis report</h1><p><span class='s {cls}'>{E(st)}</span></p>",
         f"<p class='muted'>Source: {E(ctx['source_name'])} &middot; SHA-256 {E(ctx['sha256'][:16])}&hellip; &middot; run {E(ctx['run_time'])} &middot; SWA Data Analysis Tool {__version__}</p>"]
    h.append("<h2>Rows</h2>" + _table(["Read", "Analysed", "Quarantined (excluded)", "Blank skipped"],
             [[f"{cr.n_read:,}", f"{cr.n_clean:,}", f"{cr.n_quarantined:,}", f"{cr.n_blank:,}"]], 0))
    if ctx["protected"]:
        h.append("<p class='muted'>Personal data protected in this report: " + E(", ".join(f"{n} ({m})" for n, m in ctx["protected"])) + ".</p>")
    if ctx["warnings"]:
        h.append("<h2>Warnings</h2><ul>" + "".join(f"<li>{E(w)}</li>" for w in ctx["warnings"]) + "</ul>")
    h.append("<h2>Integrity checks</h2>" + _table(
        ["Check", "Expected", "Actual", "Result"],
        [[c.name, c.expected, c.actual, "PASS" if c.passed else "FAIL"] for c in checks], 99))
    for pr in pivots:
        h.append(f"<h2>{E(pr.spec['name'])}</h2><p class='muted'>Rows: {E(pr.spec['rows'])}"
                 + (f" &middot; Columns: {E(pr.spec['columns'])}" if pr.has_cols else "") + f" &middot; {E(pr.label)}</p>")
        if not pr.row_labels:
            h.append("<p>No analysed rows.</p>")
            continue
        if pr.has_cols:
            heads = [pr.spec["rows"]] + pr.col_labels + ["Total"]
            body = [[rl] + [_fmt(v, pr.decimals) for v in pr.matrix[i]] + [_fmt(pr.row_totals[i], pr.decimals)]
                    for i, rl in enumerate(pr.row_labels)]
            body.append(["Total"] + [_fmt(v, pr.decimals) for v in pr.col_totals] + [_fmt(pr.grand_total, pr.decimals)])
        else:
            heads = [pr.spec["rows"], pr.label]
            body = [[rl, _fmt(pr.matrix[i][0], pr.decimals)] for i, rl in enumerate(pr.row_labels)]
            body.append(["Total", _fmt(pr.grand_total, pr.decimals)])
        h.append(_table(heads, body))
        if not pr.additive:
            h.append("<p class='muted'>Totals are computed on the underlying records, not by combining the figures above.</p>")
        pm = percent_matrix(pr)
        if pm is not None:
            mat, row_t, col_t, grand = pm
            pf = lambda v: "" if v is None else f"{v * 100:,.1f}%"   # noqa: E731
            title = {"row": "Percent of each row's total", "column": "Percent of each column's total", "total": "Percent of the grand total"}[pr.spec["percent_of"]]
            h.append(f"<p><b>{E(title)}</b></p>")
            if pr.has_cols:
                body = [[rl] + [pf(v) for v in mat[i]] + [pf(row_t[i])] for i, rl in enumerate(pr.row_labels)]
                body.append(["Total"] + [pf(v) for v in col_t] + [pf(grand)])
                h.append(_table([pr.spec["rows"]] + pr.col_labels + ["Total"], body))
            else:
                body = [[rl, pf(mat[i][0])] for i, rl in enumerate(pr.row_labels)] + [["Total", pf(grand)]]
                h.append(_table([pr.spec["rows"], "Percent"], body))
        png, why = _chart_png(pr)
        if png:
            h.append(f"<img alt='{E(pr.spec['name'])} chart' src='data:image/png;base64,{png}'>")
        elif why:
            h.append(f"<p class='muted'>{E(why)}</p>")
    for ir in ctx["indicators"]:
        spec, t = ir.spec, ir.spec["type"]
        h.append(f"<h2>Indicator: {E(spec['name'])}</h2><p class='muted'>{E((spec['description'] + '. ') if spec['description'] else '')}{E(describe(spec))}</p>")
        vdec = cfg["spec_by_name"][spec["values"]]["decimals"] if t in ("sum", "mean") else 0
        for dim, rows in ir.sections:
            body = []
            for row in rows:
                if t == "percent":
                    body.append([row.group, _fmt(row.n, 0), _fmt(row.d, 0), "" if row.value is None else f"{row.value * 100:,.1f}%"])
                elif t in ("sum", "mean"):
                    body.append([row.group, _fmt(row.n, 0), _fmt(row.value, max(vdec, 2) if t == "mean" else vdec)])
                else:
                    body.append([row.group, _fmt(row.n, 0)])
            h.append(f"<p><b>{E('By ' + dim if dim else 'Total')}</b></p>" + _table(ir.headers, body))
        if ir.unique:
            h.append("<p class='muted'>Distinct counts count people, not records; a person in several groups is counted once in the total.</p>")
    if cr.multiselect:
        for ms in cr.multiselect:
            h.append(f"<h2>Multi-select: {E(ms['column'])}</h2><p class='muted'>{ms['answered']:,} respondents answered; they may choose several options, so percentages add up to more than 100%.</p>"
                     + _table(["Option", "Respondents choosing it", "% of respondents"],
                              [[o["option"], f"{o['selected']:,}", "" if o["pct"] is None else f"{o['pct'] * 100:,.1f}%"] for o in ms["options"]]))
    h.append("<h2>Completeness of each column</h2>" + _table(
        ["Column", "Required?", "Missing values", "% complete"],
        [[c["column"], "yes" if c["required"] else "no", c["missing"], "" if c["pct_complete"] is None else f"{c['pct_complete'] * 100:,.1f}%"]
         for c in cr.completeness], 2))
    if cr.stats:
        h.append("<h2>Summary statistics</h2>" + _table(
            ["Column", "Count", "Missing", "Sum", "Mean", "Median", "Min", "Max"],
            [[s["column"], s["count"], s["missing"], _fmt(s["sum"], s["decimals"]), _fmt(s.get("mean"), max(s["decimals"], 2)),
              _fmt(s.get("median"), max(s["decimals"], 2)), _fmt(s.get("min"), s["decimals"]), _fmt(s.get("max"), s["decimals"])]
             for s in cr.stats]))
    if cr.quarantine_breakdown:
        h.append("<h2>Why rows were quarantined</h2>" + _table(
            ["Column", "Problem", "Rows"], [[c, p, n] for c, p, n in cr.quarantine_breakdown], 2))
        q = ctx["quar_out"].head(50)
        h.append(f"<p class='muted'>First {len(q)} of {len(ctx['quar_out'])} quarantined rows (all are in report.xlsx and quarantined_rows.csv).</p>")
        h.append(_table(list(q.columns), q.fillna("").values.tolist(), 99))
    if cfg.get("branding", True):
        h.append("<p class='muted' style='margin-top:40px;border-top:1px solid #ddd;padding-top:12px'>Created with SWA Data Analysis Tool &middot; swasamson.site</p>")
    h.append("</body></html>")
    return "".join(h)
