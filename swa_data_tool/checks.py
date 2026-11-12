"""Automatic integrity checks. Any failure marks the whole run FAILED."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .cleaning import CleanResult
from .pivots import PivotResult, percent_matrix
from .util import MISSING_LABEL


@dataclass
class Check:
    name: str
    expected: str
    actual: str
    passed: bool
    detail: str = ""


def close(a, b, rel=1e-9, abs_=1e-9) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def compare_pivots(a: PivotResult, b: PivotResult):
    """Return (n_cells_compared, mismatches[list of text])."""
    bad, n = [], 0
    if a.row_labels != b.row_labels or a.col_labels != b.col_labels:
        return 0, ["row/column labels differ between the two calculations"]
    for i, r in enumerate(a.row_labels):
        for j in range(len(a.matrix[i])):
            n += 1
            if not close(a.matrix[i][j], b.matrix[i][j]):
                bad.append(f"cell [{r}, {a.col_labels[j] if a.col_labels else ''}]: {a.matrix[i][j]} vs {b.matrix[i][j]}")
        n += 1
        if not close(a.row_totals[i], b.row_totals[i]):
            bad.append(f"row total [{r}]: {a.row_totals[i]} vs {b.row_totals[i]}")
    for j, c in enumerate(a.col_totals):
        n += 1
        if not close(c, b.col_totals[j]):
            bad.append(f"column total [{j}]: {c} vs {b.col_totals[j]}")
    n += 1
    if not close(a.grand_total, b.grand_total):
        bad.append(f"grand total: {a.grand_total} vs {b.grand_total}")
    return n, bad


def run_checks(cr: CleanResult, cfg: dict, pivots_pd: list, pivots_ind: list, inds_pd: list = (), inds_ind: list = ()) -> list:
    checks: list[Check] = []

    total = cr.n_clean + cr.n_quarantined + cr.n_blank
    checks.append(Check(
        "Row accounting: every row read is either analysed, quarantined or blank",
        str(cr.n_read), str(total), cr.n_read == total,
        f"read {cr.n_read} = clean {cr.n_clean} + quarantined {cr.n_quarantined} + blank {cr.n_blank}"))

    pct = 100.0 * cr.n_quarantined / cr.n_read if cr.n_read else 0.0
    limit = cfg["max_quarantine_pct"]
    checks.append(Check(
        "Quarantine rate within the allowed limit",
        f"<= {limit:g}%", f"{pct:.2f}%", pct <= limit + 1e-12,
        "Too many rejected rows usually means the wrong file/format or a wrong configuration."))

    miss_total = 0
    for c in cfg["columns"]:
        if c["required"]:
            col = cr.clean[c["name"]]
            if c["type"] == "category":
                miss_total += int((col == MISSING_LABEL).sum())
            elif c["type"] in ("text", "multiselect"):
                miss_total += int((col == "").sum())
            else:
                miss_total += int(col.isna().sum())
    checks.append(Check("Required columns are complete in the analysed data", "0 missing", f"{miss_total} missing", miss_total == 0))

    for c in cfg["columns"]:
        if c["unique"]:
            dups = int(cr.clean[c["name"]].duplicated().sum())
            checks.append(Check(f"Column '{c['name']}' has no duplicate values in the analysed data", "0 duplicates", f"{dups} duplicates", dups == 0))

    for pr, ind in zip(pivots_pd, pivots_ind):
        nm = pr.spec["name"]
        n, bad = compare_pivots(pr, ind)
        checks.append(Check(
            f"Pivot '{nm}': pandas result equals independent recalculation (every cell and total)",
            f"{n} values equal", f"{n - len(bad)} equal", not bad, "; ".join(bad[:5])))
        checks.append(Check(
            f"Pivot '{nm}': includes every analysed row",
            str(cr.n_clean), str(pr.rows_covered), pr.rows_covered == cr.n_clean))
        if pr.additive:
            cells = math.fsum(v for row in pr.matrix for v in row if v is not None)
            ok = close(cells, pr.grand_total, rel=1e-9, abs_=1e-6)
            checks.append(Check(f"Pivot '{nm}': cells add up to the grand total",
                                f"{pr.grand_total:,.6f}", f"{cells:,.6f}", ok))
        pm = percent_matrix(pr)
        if pm is not None:
            mat, row_t, col_t, grand = pm
            mode = pr.spec["percent_of"]
            if mode == "total":
                tot = math.fsum(v for row in mat for v in row if v is not None)
                ok, detail = (grand is None or math.isclose(tot, 1.0, abs_tol=1e-9)), "all cells together = 100%"
                actual = f"{tot * 100:.6f}%"
            elif mode == "row":
                worst = max((abs(math.fsum(v for v in row if v is not None) - 1.0) for i, row in enumerate(mat) if pr.row_totals[i] not in (None, 0)), default=0.0)
                ok, detail, actual = worst < 1e-9, "every row = 100%", f"max deviation {worst:.2e}"
            else:
                worst = max((abs(math.fsum(mat[i][j] for i in range(len(mat)) if mat[i][j] is not None) - 1.0)
                             for j in range(len(pr.col_labels)) if pr.col_totals[j] not in (None, 0)), default=0.0)
                ok, detail, actual = worst < 1e-9, "every column = 100%", f"max deviation {worst:.2e}"
            checks.append(Check(f"Pivot '{nm}': percentage table is consistent ({detail})", "100%", actual, ok))

    # multi-select: 0/1 option columns must agree with the answers as text
    for c in cfg["columns"]:
        if c["type"] != "multiselect":
            continue
        answers = [set(x.split("; ")) if x else None for x in cr.clean[c["name"]].tolist()]
        bad = []
        for o in c["options"]:
            want = float(sum(1 for a in answers if a and o in a))
            got = float(cr.clean[f"{c['name']}: {o}"].sum())
            if want != got:
                bad.append(f"{o}: {got} vs {want}")
        checks.append(Check(f"Multi-select '{c['name']}': option counts match the recorded answers",
                            "all options equal", "all equal" if not bad else "; ".join(bad[:3]), not bad))

    # indicators
    for ir, ii in zip(inds_pd, inds_ind):
        nm = ir.spec["name"]
        bad, n = [], 0
        for (dim_a, rows_a), (dim_b, rows_b) in zip(ir.sections, ii.sections):
            for ra, rb in zip(rows_a, rows_b):
                for fa, fb in ((ra.n, rb.n), (ra.d, rb.d), (ra.value, rb.value)):
                    n += 1
                    if ra.group != rb.group or not close(fa, fb):
                        bad.append(f"[{dim_a or 'total'} / {ra.group}] {fa} vs {fb}")
        checks.append(Check(f"Indicator '{nm}': pandas result equals independent recalculation",
                            f"{n} values equal", f"{n - len(bad)} equal", not bad, "; ".join(bad[:5])))
        total_rows = ir.sections[0][1]
        if ir.additive and total_rows:
            tot = total_rows[0]
            for dim, rows in ir.sections[1:]:
                parts = [("numerator" if ir.spec["type"] == "percent" else ("records" if ir.spec["type"] == "count" else "sum"),
                          math.fsum((r.n if ir.spec["type"] in ("count", "percent") else r.value) or 0.0 for r in rows),
                          (tot.n if ir.spec["type"] in ("count", "percent") else tot.value) or 0.0)]
                if ir.spec["type"] == "percent":
                    parts.append(("denominator", math.fsum((r.d or 0.0) for r in rows), tot.d or 0.0))
                for label, got, want in parts:
                    checks.append(Check(f"Indicator '{nm}': groups of '{dim}' add up to the total ({label})",
                                        f"{want:,.6f}", f"{got:,.6f}", close(got, want, rel=1e-9, abs_=1e-6)))
        if ir.spec["type"] == "percent":
            viol = [f"{r.group}" for _, rows in ir.sections for r in rows if r.value is not None and not (0 <= r.value <= 1)]
            checks.append(Check(f"Indicator '{nm}': every percentage is between 0% and 100%", "0-100%",
                                "ok" if not viol else f"outside range: {viol[:3]}", not viol))
    return checks
