"""Excel report writer.

Pivot cells are written as LIVE FORMULAS over the 'Clean Data' sheet
(exact-match SUMPRODUCT), with the Python result stored as the cached value.
So the numbers are calculated a second time by Excel itself, and the
'Checks' sheet tests that Excel's grouped totals equal Excel's own total of the
source column. (Excel cannot create native PivotTables from outside Excel; the
Clean Data sheet is an Excel Table, so Insert > PivotTable works in two clicks.)
"""
from __future__ import annotations

import math
import re

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from . import __version__
from .indicators import TOTAL_LABEL, describe
from .pivots import PivotResult, is_present, percent_matrix
from .readers import SOURCE_ROW

MAX_EXCEL_ROWS = 1_048_576
MAX_CHART_CATEGORIES = 40
FONT = "Arial"


def _q(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


def sheet_name_for(raw: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\']", "-", raw).strip()[:31] or "Pivot"
    name, k = base, 2
    while name.casefold() in used:
        suffix = f" ({k})"
        name = base[: 31 - len(suffix)] + suffix
        k += 1
    used.add(name.casefold())
    return name


def numfmt(dec: int) -> str:
    return "#,##0" if dec <= 0 else "#,##0." + "0" * dec


def _lit(text: str) -> str:
    return '"' + str(text).replace('"', '""') + '"'


def _numlit(v: float) -> str:
    return f"{v:.15g}"


def write_workbook(path, ctx: dict) -> dict:
    cfg, cr, pivots, checks = ctx["cfg"], ctx["cr"], ctx["pivots"], ctx["checks"]
    wb = xlsxwriter.Workbook(str(path), {"strings_to_formulas": False, "strings_to_urls": False,
                                         "nan_inf_to_errors": False, "default_date_format": "yyyy-mm-dd"})
    wb.formats[0].set_font_name(FONT)
    wb.formats[0].set_font_size(10)
    cache: dict = {}

    def fmt(**kw):
        key = tuple(sorted(kw.items()))
        if key not in cache:
            cache[key] = wb.add_format({"font_name": FONT, "font_size": 10, **kw})
        return cache[key]

    title = fmt(bold=True, font_size=14)
    bold = fmt(bold=True)
    head = fmt(bold=True, font_color="#FFFFFF", bg_color="#1F3864", border=1, text_wrap=True, valign="vcenter")
    text = fmt()
    wrap = fmt(text_wrap=True, valign="top")
    note = fmt(italic=True, font_color="#595959", text_wrap=True, valign="top")
    green = fmt(bold=True, bg_color="#C6EFCE", font_color="#006100")
    amber = fmt(bold=True, bg_color="#FFEB9C", font_color="#7F6000")
    red = fmt(bold=True, bg_color="#FFC7CE", font_color="#9C0006")
    datef = fmt(num_format="yyyy-mm-dd")

    status = ctx["status"]
    status_fmt = green if status == "PASSED" else amber if status == "PASSED WITH WARNINGS" else red

    n_clean = cr.n_clean
    can_write_data = n_clean <= MAX_EXCEL_ROWS - 2
    use_formulas = can_write_data and 0 < n_clean <= cfg["excel_formulas_max_rows"]

    used: set = set()
    ws_sum = wb.add_worksheet(sheet_name_for("Summary", used))
    ws_chk = wb.add_worksheet(sheet_name_for("Checks", used))
    pivot_ws = [(pr, wb.add_worksheet(sheet_name_for(pr.spec["name"], used))) for pr in pivots]
    ws_ind = wb.add_worksheet(sheet_name_for("Indicators", used)) if ctx["indicators"] else None
    ws_stats = wb.add_worksheet(sheet_name_for("Summary Stats", used))
    ws_dq = wb.add_worksheet(sheet_name_for("Data Quality", used))
    ws_data = wb.add_worksheet(sheet_name_for("Clean Data", used)) if can_write_data else None
    ws_quar = wb.add_worksheet(sheet_name_for("Quarantined Rows", used))

    # ---------------- Clean Data -----------------------------------------
    out_df = ctx["clean_out"]                      # personal data already protected
    col_order = list(out_df.columns)
    col_idx = {nm: i for i, nm in enumerate(col_order)}
    col_spec = cfg["spec_by_name"]
    data_sheet = "Clean Data"

    def rng(colname: str) -> str:
        L = xl_col_to_name(col_idx[colname])
        return f"{_q(data_sheet)}!${L}$2:${L}${n_clean + 1}"

    if ws_data is not None:
        df = out_df
        ws_data.freeze_panes(1, 1)
        if n_clean:
            ws_data.add_table(0, 0, n_clean, len(col_order) - 1, {
                "name": "CleanData", "style": "Table Style Medium 2", "autofilter": True,
                "columns": [{"header": h, "header_format": head} for h in col_order]})
        else:
            for j, h in enumerate(col_order):
                ws_data.write_string(0, j, h, head)
        col_formats = {}
        for nm in col_order:
            spec = col_spec.get(nm)
            if spec and spec["type"] == "number":
                col_formats[nm] = fmt(num_format=numfmt(spec["decimals"]))
            elif spec and spec["type"] == "date":
                col_formats[nm] = datef
            else:
                col_formats[nm] = text
        for j, nm in enumerate(col_order):
            values = df[nm].tolist()
            f = col_formats[nm]
            spec = col_spec.get(nm)
            for i, v in enumerate(values, start=1):
                if spec and spec["type"] == "number":
                    if v == v:  # not NaN
                        ws_data.write_number(i, j, v, f)
                elif spec and spec["type"] == "date":
                    if v is not None and v == v:
                        ws_data.write_datetime(i, j, v.to_pydatetime(), f)
                elif nm == SOURCE_ROW:
                    ws_data.write_number(i, j, v, f)
                else:
                    ws_data.write_string(i, j, str(v), f)
            width = 12 if (spec and spec["type"] in ("number", "date")) or nm == SOURCE_ROW else \
                min(40, max(12, int(df[nm].astype(str).str.len().max() or 10) + 2)) if n_clean else 14
            ws_data.set_column(j, j, max(width, len(nm) + 2))

    # ---------------- Pivot sheets ---------------------------------------
    pivot_info = []     # for live checks: (pr, sheet name, grand cell ref)
    for pr, ws in pivot_ws:
        sname = ws.get_name()
        spec = pr.spec
        ws.write_string(0, 0, spec["name"], title)
        desc = f"Rows: {spec['rows']}" + (f"   |   Columns: {spec['columns']}" if spec.get("columns") else "") + f"   |   Values: {pr.label}"
        ws.write_string(1, 0, desc, fmt(italic=True, font_color="#595959"))   # no wrapping: keeps row heights predictable
        hdr, first = 3, 4
        nrows = len(pr.row_labels)
        ncols = len(pr.col_labels) if pr.has_cols else 1
        ws.write_string(hdr, 0, spec["rows"], head)
        if pr.has_cols:
            for j, lbl in enumerate(pr.col_labels):
                ws.write_string(hdr, 1 + j, lbl, head)
            ws.write_string(hdr, 1 + ncols, "Total", head)
        else:
            ws.write_string(hdr, 1, pr.label, head)
        if nrows == 0:
            ws.write_string(first, 0, "No analysed rows - nothing to show.", note)
            continue
        vf = fmt(num_format=numfmt(pr.decimals))
        vfb = fmt(num_format=numfmt(pr.decimals), bold=True, top=1)
        lab = fmt(bold=False)
        labb = fmt(bold=True, top=1)
        last = first + nrows - 1
        total_r = last + 1
        agg, vcol = spec["agg"], spec.get("values")
        formula_ok = use_formulas and agg in ("sum", "count", "count_rows", "mean")
        present = []
        if vcol and agg == "count":
            vr = rng(vcol)
            present = [f'--({vr}<>"")'] + ([f'--({vr}<>"(Missing)")'] if col_spec[vcol]["type"] == "category" else [])

        def conds(r_ref=None, c_ref=None):
            parts = []
            if r_ref:
                parts.append(f"--EXACT({rng(spec['rows'])},{r_ref})")
            if c_ref:
                parts.append(f"--EXACT({rng(spec['columns'])},{c_ref})")
            return parts

        def cell_formula(r_ref, c_ref):
            cs = conds(r_ref, c_ref)
            if agg == "sum":
                return "=SUMPRODUCT(" + ",".join(cs + [rng(vcol)]) + ")"
            if agg == "count":
                return "=SUMPRODUCT(" + ",".join(cs + present) + ")"
            if agg == "count_rows":
                return "=SUMPRODUCT(" + ",".join(cs) + ")"
            num = "SUMPRODUCT(" + ",".join(cs + [rng(vcol)]) + ")"
            den = "SUMPRODUCT(" + ",".join(cs + [f'--({rng(vcol)}<>"")']) + ")"
            return f'=IFERROR({num}/{den},"")'

        def put(r, c, formula, value, f):
            if formula_ok and (value is not None or agg == "mean"):
                ws.write_formula(r, c, formula, f, "" if value is None else value)
            elif value is None:
                ws.write_blank(r, c, None, f)
            else:
                ws.write_number(r, c, value, f)

        for i, rl in enumerate(pr.row_labels):
            r = first + i
            ws.write_string(r, 0, rl, lab)
            for j in range(ncols):
                c_ref = f"{xl_col_to_name(1 + j)}${hdr + 1}" if pr.has_cols else None
                put(r, 1 + j, cell_formula(f"$A{r + 1}", c_ref), pr.matrix[i][j], vf)
            if pr.has_cols:
                tc = 1 + ncols
                if pr.additive and formula_ok:
                    ws.write_formula(r, tc, f"=SUM({xl_col_to_name(1)}{r + 1}:{xl_col_to_name(ncols)}{r + 1})", vf, pr.row_totals[i])
                else:
                    put(r, tc, cell_formula(f"$A{r + 1}", None), pr.row_totals[i], vf)
        ws.write_string(total_r, 0, "Total", labb)
        if pr.has_cols:
            for j in range(ncols):
                L = xl_col_to_name(1 + j)
                if pr.additive and formula_ok:
                    ws.write_formula(total_r, 1 + j, f"=SUM({L}{first + 1}:{L}{last + 1})", vfb, pr.col_totals[j])
                else:
                    put(total_r, 1 + j, cell_formula(None, f"{L}${hdr + 1}"), pr.col_totals[j], vfb)
            tc = 1 + ncols
            L = xl_col_to_name(tc)
            if pr.additive and formula_ok:
                ws.write_formula(total_r, tc, f"=SUM({L}{first + 1}:{L}{last + 1})", vfb, pr.grand_total)
            else:
                put(total_r, tc, cell_formula(None, None), pr.grand_total, vfb)
            grand_ref = f"{_q(sname)}!${L}${total_r + 1}"
        else:
            if pr.additive and formula_ok:
                ws.write_formula(total_r, 1, f"=SUM(B{first + 1}:B{last + 1})", vfb, pr.grand_total)
            else:
                put(total_r, 1, cell_formula(None, None), pr.grand_total, vfb)
            grand_ref = f"{_q(sname)}!$B${total_r + 1}"
        ws.set_column(0, 0, max(14, min(40, max(len(x) for x in pr.row_labels) + 2, len(spec["rows"]) + 2)))
        ws.set_column(1, ncols + 1, 16)
        ws.set_row(hdr, 30)
        ws.freeze_panes(first, 1)
        if pr.additive and formula_ok:
            pivot_info.append((pr, sname, grand_ref))
        notes = []
        if not formula_ok:
            reason = ("this aggregation (min/max/median/distinct count) cannot be a simple Excel formula" if agg in ("min", "max", "median", "count_unique")
                      else f"the data has more than {cfg['excel_formulas_max_rows']:,} rows (setting excel_formulas_max_rows)")
            notes.append(f"Values are fixed numbers (not live formulas) because {reason}. Python verified them with a second, independent calculation.")
        else:
            notes.append("Cells are live Excel formulas over the 'Clean Data' sheet; Python's verified result is stored as the starting value.")
        if not pr.additive:
            notes.append("Totals are computed on the underlying records, never by combining the figures above"
                         + (" - a person who appears in several groups is counted once in the total." if agg == "count_unique" else "."))
        ws.merge_range(total_r + 2, 0, total_r + 2, max(4, ncols + 1), " ".join(notes), note)
        ws.set_row(total_r + 2, 42)
        chart_row = total_r + 4
        pm = percent_matrix(pr)
        if pm is not None:
            mat, row_t, col_t, grand = pm
            mode = spec["percent_of"]
            pf = fmt(num_format="0.0%")
            pfb = fmt(num_format="0.0%", bold=True, top=1)
            p0 = total_r + 4
            ws.write_string(p0, 0, {"row": "Percent of each row's total", "column": "Percent of each column's total",
                                    "total": "Percent of the grand total"}[mode], bold)
            ph = p0 + 1
            ws.write_string(ph, 0, spec["rows"], head)
            if pr.has_cols:
                for j, lbl in enumerate(pr.col_labels):
                    ws.write_string(ph, 1 + j, lbl, head)
                ws.write_string(ph, 1 + ncols, "Total", head)
            else:
                ws.write_string(ph, 1, "Percent", head)
            pfirst = ph + 1

            def pw(r, c, formula, value, f):
                if formula_ok and formula:
                    ws.write_formula(r, c, formula, f, "" if value is None else value)
                elif value is None:
                    ws.write_blank(r, c, None, f)
                else:
                    ws.write_number(r, c, value, f)

            for i, rl in enumerate(pr.row_labels):
                r = pfirst + i
                src_r = first + i + 1          # Excel row number in the main table
                ws.write_string(r, 0, rl, lab)
                for j in range(ncols):
                    L = xl_col_to_name(1 + j)
                    if mode == "row":
                        den = f"${xl_col_to_name(1 + ncols)}{src_r}"
                    elif mode == "column":
                        den = f"{L}${total_r + 1}"
                    else:
                        den = f"${xl_col_to_name(1 + ncols) if pr.has_cols else 'B'}${total_r + 1}"
                    pw(r, 1 + j, f'=IF({den}=0,"",{L}{src_r}/{den})', mat[i][j], pf)
                if pr.has_cols:
                    tc = xl_col_to_name(1 + ncols)
                    den = f"{tc}${total_r + 1}" if mode != "row" else f"{tc}{src_r}"
                    pw(r, 1 + ncols, f'=IF({den}=0,"",{tc}{src_r}/{den})', row_t[i], pf)
            pt = pfirst + nrows
            ws.write_string(pt, 0, "Total", labb)
            if pr.has_cols:
                for j in range(ncols):
                    L = xl_col_to_name(1 + j)
                    den = f"{L}${total_r + 1}" if mode == "column" else f"${xl_col_to_name(1 + ncols)}${total_r + 1}"
                    pw(pt, 1 + j, f'=IF({den}=0,"",{L}${total_r + 1}/{den})', col_t[j], pfb)
                tc = xl_col_to_name(1 + ncols)
                pw(pt, 1 + ncols, f'=IF({tc}${total_r + 1}=0,"",{tc}${total_r + 1}/{tc}${total_r + 1})', grand, pfb)
            else:
                pw(pt, 1, f'=IF(B${total_r + 1}=0,"",B${total_r + 1}/B${total_r + 1})', grand, pfb)
            chart_row = pt + 4
        # chart
        chart_type = spec["chart"]
        skip_reason = None
        if chart_type != "none":
            if nrows > MAX_CHART_CATEGORIES:
                skip_reason = f"Chart omitted: {nrows} categories is too many to read. Use sort: value_desc and a more grouped field."
            elif chart_type == "pie" and any((v or 0) < 0 for row in pr.matrix for v in row):
                skip_reason = "Chart omitted: a pie chart cannot show negative values."
        if chart_type != "none" and not skip_reason:
            ctype = {"column": ("column", None), "column_stacked": ("column", "stacked"), "bar": ("bar", None),
                     "line": ("line", None), "pie": ("pie", None)}[chart_type]
            ch = wb.add_chart({"type": ctype[0], **({"subtype": ctype[1]} if ctype[1] else {})})
            if pr.has_cols:
                for j, lbl in enumerate(pr.col_labels):
                    ch.add_series({"name": [sname, hdr, 1 + j], "categories": [sname, first, 0, last, 0],
                                   "values": [sname, first, 1 + j, last, 1 + j]})
            else:
                ser = {"name": pr.label, "categories": [sname, first, 0, last, 0], "values": [sname, first, 1, last, 1]}
                if chart_type == "pie":
                    ser["data_labels"] = {"percentage": True}
                ch.add_series(ser)
            ch.set_title({"name": f"{spec['name']}", "name_font": {"name": FONT, "size": 12}})
            if chart_type != "pie":
                ch.set_x_axis({"name": spec["rows"], "name_font": {"name": FONT, "size": 10}})
                ch.set_y_axis({"name": pr.label, "num_format": numfmt(pr.decimals), "name_font": {"name": FONT, "size": 10},
                               "major_gridlines": {"visible": True, "line": {"color": "#D9D9D9"}}})
            ch.set_legend({"position": "bottom"} if (pr.has_cols or chart_type == "pie") else {"none": True})
            ch.set_size({"width": 760, "height": 400})
            ws.insert_chart(chart_row, 0, ch)
        elif skip_reason:
            ws.write_string(chart_row, 0, skip_reason, note)

    # ---------------- Indicators -----------------------------------------
    ind_live = []
    if ws_ind is not None:
        iname = ws_ind.get_name()

        def filt_factors(filters):
            out = []
            for f in filters:
                R, t, op, v = rng(f["column"]), col_spec[f["column"]]["type"], f["op"], f["value"]
                if t in ("category", "text"):
                    if op == "equals":
                        out.append(f"--EXACT({R},{_lit(v)})")
                    elif op == "not_equals":
                        out.append(f"(1-EXACT({R},{_lit(v)}))")
                    else:
                        ssum = "+".join(f"EXACT({R},{_lit(x)})" for x in v)
                        out.append(f"--(({ssum})>0)" if op == "in" else f"--(({ssum})=0)")
                else:
                    out.append(f'--({R}<>"")')
                    if op in ("in", "not_in"):
                        ssum = "+".join(f"({R}={_numlit(x)})" for x in v)
                        out.append(f"--(({ssum})>0)" if op == "in" else f"--(({ssum})=0)")
                    else:
                        sym = {"equals": "=", "not_equals": "<>", "gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}[op]
                        out.append(f"--({R}{sym}{_numlit(v)})")
            return out

        ws_ind.write_string(0, 0, "Programme indicators", title)
        ws_ind.write_string(1, 0, "Each indicator is calculated twice by independent methods. Where possible the cells are live Excel "
                                  "formulas over 'Clean Data'. Percentages are stored as fractions and shown as %.", fmt(italic=True, font_color="#595959"))
        ws_ind.set_column(0, 0, 22); ws_ind.set_column(1, 1, 30); ws_ind.set_column(2, 5, 18)
        r = 3
        h2 = fmt(bold=True, font_size=12)
        for ir in ctx["indicators"]:
            spec, t = ir.spec, ir.spec["type"]
            ws_ind.write_string(r, 0, spec["name"], h2); r += 1
            ws_ind.merge_range(r, 0, r, 5, (spec["description"] + ". " if spec["description"] else "") + describe(spec), note)
            ws_ind.set_row(r, 44); r += 1
            hdrs = ["Disaggregated by", "Group"] + ir.headers[1:]
            for j, h in enumerate(hdrs):
                ws_ind.write_string(r, j, h, head)
            r += 1
            can_formula = use_formulas and not ir.unique
            vdec = col_spec[spec["values"]]["decimals"] if t in ("sum", "mean") else 0
            f_int = fmt(num_format="#,##0")
            f_val = fmt(num_format=numfmt(max(vdec, 2) if t == "mean" else vdec))
            f_pct = fmt(num_format="0.0%")
            total_row_excel = None
            for dim, rows in ir.sections:
                sec_first = r
                for row in rows:
                    ws_ind.write_string(r, 0, dim or "Total", text)
                    ws_ind.write_string(r, 1, row.group, bold if dim is None else text)
                    xr = r + 1
                    gf = [] if dim is None else [f"--EXACT({rng(dim)},{_lit(row.group)})"]
                    wf = filt_factors(spec["where"])
                    af = filt_factors(spec["among"])

                    def put_f(col, formula, value, f, ok=True):
                        if can_formula and ok and len(formula) < 7500:
                            ws_ind.write_formula(r, col, formula, f, "" if value is None else value)
                        elif value is None:
                            ws_ind.write_blank(r, col, None, f)
                        else:
                            ws_ind.write_number(r, col, value, f)

                    def sp(factors, extra=None):
                        parts = factors + ([extra] if extra else [])
                        if not parts:
                            return f"ROWS({rng(SOURCE_ROW)})"
                        return "SUMPRODUCT(" + ",".join(parts) + ")"

                    if t == "count":
                        put_f(2, "=" + sp(wf + gf), row.n, f_int)
                    elif t == "count_unique":
                        ws_ind.write_number(r, 2, row.n, f_int)
                    elif t in ("sum", "mean"):
                        vr = rng(spec["values"])
                        put_f(2, "=" + sp(wf + gf, f'--({vr}<>"")'), row.n, f_int)
                        if t == "sum":
                            put_f(3, "=" + sp(wf + gf, vr), row.value, f_val)
                        else:
                            put_f(3, f'=IF(C{xr}=0,"",{sp(wf + gf, vr)}/C{xr})', row.value, f_val)
                    else:
                        put_f(2, "=" + sp(af + wf + gf), row.n, f_int)
                        put_f(3, "=" + sp(af + gf), row.d, f_int)
                        put_f(4, f'=IF(D{xr}=0,"",C{xr}/D{xr})', row.value, f_pct)
                    if dim is None:
                        total_row_excel = xr
                    r += 1
                if dim is not None and ir.additive and can_formula and rows and total_row_excel:
                    cols_to_check = {"count": [(2, "records")], "sum": [(3, "sum")], "percent": [(2, "numerator"), (3, "denominator")]}[t]
                    for ci, lbl in cols_to_check:
                        L = xl_col_to_name(ci)
                        tot_row = ir.sections[0][1][0]
                        want = (tot_row.value if t == "sum" else (tot_row.n if ci == 2 else tot_row.d)) or 0.0
                        got = math.fsum(((x.value if t == "sum" else (x.n if ci == 2 else x.d)) or 0.0) for x in rows)
                        ind_live.append({"label": f"Indicator '{spec['name']}': groups of '{dim}' add up to the total ({lbl})",
                                         "total": f"={_q(iname)}!${L}${total_row_excel}",
                                         "sum": f"=SUM({_q(iname)}!${L}${sec_first + 1}:${L}${r})", "want": want, "got": got})
                r += 0
            if ir.unique:
                ws_ind.merge_range(r, 0, r, 5, "Distinct counts (people, not records) are fixed values verified by a second independent calculation; "
                                               "a person who appears in several groups is counted once in the total.", note)
                ws_ind.set_row(r, 30); r += 1
            r += 2
        ws_ind.freeze_panes(3, 0)

    # ---------------- Checks ---------------------------------------------
    ws_chk.write_string(0, 0, "Integrity checks", title)
    ws_chk.write_string(1, 0, "Every check must PASS. Any FAIL means do not rely on the numbers until it is explained.", note)
    for j, h in enumerate(["#", "Check", "Expected", "Actual", "Result", "Details"]):
        ws_chk.write_string(3, j, h, head)
    r = 4
    for k, ck in enumerate(checks, start=1):
        ws_chk.write_number(r, 0, k, text)
        ws_chk.write_string(r, 1, ck.name, wrap)
        ws_chk.write_string(r, 2, ck.expected, text)
        ws_chk.write_string(r, 3, ck.actual, text)
        ws_chk.write_string(r, 4, "PASS" if ck.passed else "FAIL", green if ck.passed else red)
        ws_chk.write_string(r, 5, ck.detail, wrap)
        r += 1
    live_all_pass = True
    if pivot_info:
        r += 1
        ws_chk.write_string(r, 0, "Live Excel cross-checks (recalculated by Excel whenever the file is opened or Clean Data is edited)", bold)
        r += 1
        for pr, sname, grand_ref in pivot_info:
            agg, vcol = pr.spec["agg"], pr.spec.get("values")
            if agg == "sum":
                exp_f, exp_v, nm = f"=SUM({rng(vcol)})", math.fsum(v for v in ctx["cr"].clean[vcol].tolist() if v == v), "Σ of source column"
            elif agg == "count":
                vr = rng(vcol)
                facs = [f'--({vr}<>"")'] + ([f'--({vr}<>"(Missing)")'] if col_spec[vcol]["type"] == "category" else [])
                exp_f = "=SUMPRODUCT(" + ",".join(facs) + ")"
                exp_v = float(sum(1 for v in ctx["cr"].clean[vcol].tolist() if is_present(v)))
                nm = "count of non-blank source values"
            else:
                exp_f, exp_v, nm = f"=ROWS({rng(SOURCE_ROW)})", float(n_clean), "number of clean rows"
            ws_chk.write_string(r, 1, f"Pivot '{pr.spec['name']}': Excel grand total equals {nm}", wrap)
            ws_chk.write_formula(r, 2, exp_f, fmt(num_format=numfmt(pr.decimals)), exp_v)
            ws_chk.write_formula(r, 3, f"={grand_ref}", fmt(num_format=numfmt(pr.decimals)), pr.grand_total)
            ok = math.isclose(exp_v, pr.grand_total, rel_tol=1e-9, abs_tol=1e-6)
            live_all_pass &= ok
            ws_chk.write_formula(r, 4, f'=IF(ABS(C{r + 1}-D{r + 1})<=0.000001*MAX(1,ABS(C{r + 1})),"PASS","FAIL")',
                                 green if ok else red, "PASS" if ok else "FAIL")
            ws_chk.write_string(r, 5, "Live formula check", text)
            r += 1
    if ind_live:
        if not pivot_info:
            r += 1
            ws_chk.write_string(r, 0, "Live Excel cross-checks (recalculated by Excel whenever the file is opened or Clean Data is edited)", bold)
            r += 1
        for it in ind_live:
            ws_chk.write_string(r, 1, it["label"], wrap)
            ws_chk.write_formula(r, 2, it["total"], fmt(num_format="#,##0.00"), it["want"])
            ws_chk.write_formula(r, 3, it["sum"], fmt(num_format="#,##0.00"), it["got"])
            ok = math.isclose(it["want"], it["got"], rel_tol=1e-9, abs_tol=1e-6)
            live_all_pass &= ok
            ws_chk.write_formula(r, 4, f'=IF(ABS(C{r + 1}-D{r + 1})<=0.000001*MAX(1,ABS(C{r + 1})),"PASS","FAIL")',
                                 green if ok else red, "PASS" if ok else "FAIL")
            ws_chk.write_string(r, 5, "Live formula check", text)
            r += 1
    ws_chk.set_column(0, 0, 5); ws_chk.set_column(1, 1, 70); ws_chk.set_column(2, 3, 20)
    ws_chk.set_column(4, 4, 10); ws_chk.set_column(5, 5, 60)
    ws_chk.conditional_format(4, 4, r, 4, {"type": "cell", "criteria": "==", "value": '"FAIL"', "format": red})
    ws_chk.conditional_format(4, 4, r, 4, {"type": "cell", "criteria": "==", "value": '"PASS"', "format": green})
    ws_chk.freeze_panes(4, 0)

    # ---------------- Summary --------------------------------------------
    ws_sum.set_column(0, 0, 30); ws_sum.set_column(1, 1, 90)
    ws_sum.write_string(0, 0, f"{cfg['dataset_name']} - analysis report", title)
    rows = [
        ("Status", status),
        ("Source file", ctx["source_name"]),
        ("Source file SHA-256", ctx["sha256"]),
        ("Run time", ctx["run_time"]),
        ("Tool version", f"SWA Data Analysis Tool {__version__}"),
        ("Configuration", f"{ctx['config_name']} (SHA-256 {cfg['_config_sha256'][:16]}...)"),
        ("Rows read", f"{cr.n_read:,}"),
        ("Rows analysed", f"{cr.n_clean:,}"),
        ("Rows quarantined (excluded)", f"{cr.n_quarantined:,}"),
        ("Blank rows skipped", f"{cr.n_blank:,}"),
        ("Pivot tables", ", ".join(p.spec["name"] for p in pivots) or "(none configured)"),
        ("Pivot cells", "Live Excel formulas, double-checked" if use_formulas else "Fixed values (see note on each pivot sheet)"),
        ("Indicators", ", ".join(i.spec["name"] for i in ctx["indicators"]) or "(none configured)"),
        ("Protected personal data", ", ".join(f"{n} ({m})" for n, m in ctx["protected"]) or "None configured"),
    ]
    if cfg["organisation"]:
        rows.insert(0, ("Organisation", cfg["organisation"]))
    if cfg.get("branding", True):
        rows.append(("Created with", "SWA Data Analysis Tool - swasamson.site"))
    for i, (k, v) in enumerate(rows, start=2):
        ws_sum.write_string(i, 0, k, bold)
        ws_sum.write_string(i, 1, str(v), status_fmt if k == "Status" else wrap)
    i = len(rows) + 2
    ws_sum.write_string(i, 0, "Live Excel checks", bold)
    all_cached = "ALL PASS" if (live_all_pass and all(c.passed for c in checks)) else "SEE CHECKS SHEET"
    ws_sum.write_formula(i, 1, '=IF(COUNTIF(Checks!E:E,"FAIL")=0,"ALL PASS","SEE CHECKS SHEET")',
                         green if all_cached == "ALL PASS" else red, all_cached)
    i += 2
    ws_sum.write_string(i, 0, "Warnings", bold)
    if ctx["warnings"]:
        for w in ctx["warnings"]:
            ws_sum.write_string(i, 1, "• " + w, wrap)
            i += 1
    else:
        ws_sum.write_string(i, 1, "None", text)

    # ---------------- Summary Stats --------------------------------------
    ws_stats.write_string(0, 0, "Summary statistics (analysed rows only)", title)
    heads = ["Column", "Count", "Missing", "Sum", "Mean", "Median", "Std dev (sample)", "Min", "Max"]
    for j, h in enumerate(heads):
        ws_stats.write_string(2, j, h, head)
    for i, s in enumerate(cr.stats, start=3):
        nf = fmt(num_format=numfmt(s["decimals"]))
        ws_stats.write_string(i, 0, s["column"], text)
        ws_stats.write_number(i, 1, s["count"], fmt(num_format="#,##0"))
        ws_stats.write_number(i, 2, s["missing"], fmt(num_format="#,##0"))
        ws_stats.write_number(i, 3, s["sum"], nf)
        for j, key in enumerate(["mean", "median", "std", "min", "max"], start=4):
            v = s.get(key)
            if v is None:
                ws_stats.write_blank(i, j, None, text)
            else:
                ws_stats.write_number(i, j, v, fmt(num_format=numfmt(max(s["decimals"], 2)) if key in ("mean", "median", "std") else numfmt(s["decimals"])))
    if not cr.stats:
        ws_stats.write_string(3, 0, "No number columns configured.", note)
    ws_stats.set_column(0, 0, 34); ws_stats.set_column(1, 8, 16)
    r = max(len(cr.stats), 1) + 5
    for ms in cr.multiselect:
        ws_stats.write_string(r, 0, f"Multi-select question: {ms['column']}  ({ms['answered']:,} respondents answered)", bold); r += 1
        for j, h in enumerate(["Option", "Respondents choosing it", "% of respondents who answered"]):
            ws_stats.write_string(r, j, h, head)
        ws_stats.set_row(r, 30); r += 1
        for o in ms["options"]:
            ws_stats.write_string(r, 0, o["option"], text)
            ws_stats.write_number(r, 1, o["selected"], fmt(num_format="#,##0"))
            if o["pct"] is None:
                ws_stats.write_blank(r, 2, None, text)
            else:
                ws_stats.write_number(r, 2, o["pct"], fmt(num_format="0.0%"))
            r += 1
        ws_stats.write_string(r, 0, "Respondents can choose several options, so the percentages add up to more than 100%.", note)
        r += 2

    # ---------------- Data Quality ---------------------------------------
    ws_dq.set_column(0, 0, 28); ws_dq.set_column(1, 1, 55); ws_dq.set_column(2, 4, 18)
    ws_dq.write_string(0, 0, "Data quality report", title)
    r = 2
    ws_dq.write_string(r, 0, "Why rows were quarantined", bold); r += 1
    if cr.quarantine_breakdown:
        for j, h in enumerate(["Column", "Problem", "Rows"]):
            ws_dq.write_string(r, j, h, head)
        r += 1
        for col, prob, n in cr.quarantine_breakdown:
            ws_dq.write_string(r, 0, col, text); ws_dq.write_string(r, 1, prob, text); ws_dq.write_number(r, 2, n, text); r += 1
        ws_dq.write_string(r, 0, "A row can have several problems, so counts may add up to more than the quarantined rows.", note); r += 1
    else:
        ws_dq.write_string(r, 0, "None - every non-blank row passed validation.", text); r += 1
    r += 1
    ws_dq.write_string(r, 0, "Completeness of each column (analysed rows)", bold); r += 1
    for j, h in enumerate(["Column", "Required?", "Missing values", "% complete"]):
        ws_dq.write_string(r, j, h, head)
    r += 1
    for cp in cr.completeness:
        ws_dq.write_string(r, 0, cp["column"], text)
        ws_dq.write_string(r, 1, "yes" if cp["required"] else "no", text)
        ws_dq.write_number(r, 2, cp["missing"], fmt(num_format="#,##0"))
        if cp["pct_complete"] is None:
            ws_dq.write_blank(r, 3, None, text)
        else:
            ws_dq.write_number(r, 3, cp["pct_complete"], fmt(num_format="0.0%"))
        r += 1
    r += 1
    ws_dq.write_string(r, 0, "Values spelled in more than one way", bold); r += 1
    if cr.near_duplicates:
        for j, h in enumerate(["Column", "Spellings found (with row counts)"]):
            ws_dq.write_string(r, j, h, head)
        r += 1
        for nd in cr.near_duplicates:
            ws_dq.write_string(r, 0, nd["column"], text)
            ws_dq.write_string(r, 1, "; ".join(f"{k} ({v})" for k, v in nd["spellings"].items()), wrap); r += 1
    else:
        ws_dq.write_string(r, 0, "None", text); r += 1
    r += 1
    ws_dq.write_string(r, 0, f"Unusual numeric values (flagged only, kept in analysis; {cr.outlier_total} total)", bold); r += 1
    if cr.outliers:
        for j, h in enumerate(["Column", "Source row", "Value", "Normal range low", "Normal range high"]):
            ws_dq.write_string(r, j, h, head)
        r += 1
        for o in cr.outliers:
            dec = next(c["decimals"] for c in cfg["columns"] if c["name"] == o["column"])
            nf = fmt(num_format=numfmt(dec))
            ws_dq.write_string(r, 0, o["column"], text); ws_dq.write_number(r, 1, o["source_row"], text)
            ws_dq.write_number(r, 2, o["value"], nf); ws_dq.write_number(r, 3, o["low"], nf); ws_dq.write_number(r, 4, o["high"], nf); r += 1
        if cr.outlier_total > len(cr.outliers):
            ws_dq.write_string(r, 0, f"Only the first {len(cr.outliers)} are listed.", note); r += 1
    else:
        ws_dq.write_string(r, 0, "None", text); r += 1
    r += 1
    ws_dq.write_string(r, 0, "All warnings", bold); r += 1
    for w in ctx["warnings"] or ["None"]:
        ws_dq.write_string(r, 0, w, text); r += 1

    # ---------------- Quarantined rows -----------------------------------
    qd = ctx["quar_out"]                          # personal data already protected
    qcols = list(qd.columns)
    for j, h in enumerate(qcols):
        ws_quar.write_string(0, j, h, head)
    for i, row in enumerate(qd.itertuples(index=False), start=1):
        for j, v in enumerate(row):
            if j == 0:
                ws_quar.write_number(i, j, int(v), text)
            else:
                ws_quar.write_string(i, j, "" if v is None else str(v), wrap if j == 1 else text)
    ws_quar.set_column(0, 0, 11); ws_quar.set_column(1, 1, 60); ws_quar.set_column(2, max(2, len(qcols)), 18)
    ws_quar.freeze_panes(1, 2)
    if len(qd):
        ws_quar.autofilter(0, 0, len(qd), len(qcols) - 1)

    for w in wb.worksheets():            # print-friendly: landscape, one page wide
        w.set_landscape()
        w.set_paper(9)
        w.fit_to_pages(1, 0)
    ws_sum.activate()
    wb.close()
    return {"formulas": use_formulas, "clean_data_sheet": ws_data is not None}
