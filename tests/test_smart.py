import csv
import io
import json
import random
import shutil
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock

import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from swa_data_tool import smart_qa  # noqa: E402
from swa_data_tool.smart import MAX_SMART_ROWS, load_qa_context, run_smart  # noqa: E402
from swa_data_tool.smart_analyze import analyze  # noqa: E402
from swa_data_tool.smart_clean import build_dataset  # noqa: E402
from swa_data_tool.smart_load import SmartError, scan_file  # noqa: E402
from swa_data_tool.smart_qa import ask, context_from_dataset  # noqa: E402

REAL_EXAM = Path("/mnt/user-data/uploads/Data-Analyst-Exam-1.xlsx")
COUNTRY = {"BMW": "Germany", "Audi": "Germany", "Mercedes": "Germany", "Toyota": "Japan", "Lexus": "Japan", "Tesla": "USA", "Volvo": "Sweden"}
MODELS = ["BMW X5", "BMW X3", "Audi A3", "Audi Q7", "Mercedes S-Class", "Toyota Camry", "Lexus RX350", "Tesla Model 3", "Volvo XC90", "BMW 3 Series"]
COLORS = ["Black", "White", "Blue", "Red", "Silver", "Yellow"]
OWNERS = ["1st owner", "2nd owner", "3rd owner"]


def exam_rows(seed=1, n=40):
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        car = rnd.choice(MODELS)
        color = rnd.choice(COLORS)
        if i % 7 == 0:
            color = " " + color + " "                      # stray spaces
        rows.append([car, rnd.randint(2012, 2021), rnd.randint(20000, 200000), color, rnd.choice(["Automatic", "Manual"]),
                     rnd.choice(OWNERS), f"${rnd.randint(9000, 90000)}"])
    rows[3][3] = "Yellow"                                    # make Yellow rare but present
    return rows


def make_exam_workbook(path, rows=None, with_solution=True, with_lookup=True):
    rows = rows or exam_rows()
    wb = openpyxl.Workbook()
    q = wb.active
    q.title = "Questions"
    q["B1"] = "You are the data analyst. Answer the following:"
    q["K1"] = "All rights reserved"
    for i, t in enumerate(["What's the most popular car manufacturer?", "What's the most popular manufacturing country? (Refer to the 'Manufacturing country' sheet)",
                           "What's the least popular color for cars?", "What's the average price per owner?"], start=1):
        q.cell(1 + i, 1, i)
        q.cell(1 + i, 2, t)
    header = ["Car", "Year", "KM Driven", "Color", "Transmission", "Owner", "Price (USD)"]
    for j, h in enumerate(header):
        q.cell(7, 2 + j, h)
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            q.cell(8 + i, 2 + j, v)
    if with_lookup:
        m = wb.create_sheet("Manufacturing country")
        m["A1"], m["B1"] = "Company", "Country"
        for i, (k, v) in enumerate(COUNTRY.items(), start=2):
            m.cell(i, 1, k)
            m.cell(i, 2, v)
    if with_solution:
        s = wb.create_sheet("Solution")
        r0 = 1
        for _ in range(3):                                    # the same table repeated, with tutorial text between
            s.cell(r0, 2, "Step by step: this is how to do it")
            r0 += 2
            for j, h in enumerate(header):
                s.cell(r0, 2 + j, h)
            for i, r in enumerate(rows):
                for j, v in enumerate(r):
                    s.cell(r0 + 1 + i, 2 + j, v)
            r0 += len(rows) + 3
    wb.save(path)


def truth(rows):
    clean_color = [r[3].strip() for r in rows]
    brands = [r[0].split(" ")[0] for r in rows]
    by_owner = defaultdict(list)
    for r in rows:
        by_owner[r[5]].append(float(r[6].replace("$", "")))
    return {"brand": Counter(brands), "country": Counter(COUNTRY[b] for b in brands), "color": Counter(clean_color),
            "avg_owner": {k: sum(v) / len(v) for k, v in by_owner.items()}}


class Base(unittest.TestCase):
    def tmp(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def exam_ctx(self, **kw):
        d = self.tmp()
        make_exam_workbook(d / "exam.xlsx", **kw)
        scan = scan_file(d / "exam.xlsx")
        ds = build_dataset(scan)
        return scan, ds, context_from_dataset(ds)


class Loading(Base):
    def test_finds_offset_table_lookup_questions_and_ignores_duplicates(self):
        scan, ds, _ = self.exam_ctx()
        b = scan.primary[0]
        self.assertEqual((b.sheet, b.ref), ("Questions", "B7:H47"))
        self.assertEqual(len(b.rows), 40)
        self.assertEqual([lk.sheet for lk in scan.lookups], ["Manufacturing country"])
        self.assertEqual(len(scan.questions), 4)
        self.assertTrue(any("Solution" in n for n in scan.notes))          # repeated tables are reported, not analysed
        self.assertEqual(len(ds.df), 40)                                   # NOT 40 x 4

    def test_header_not_in_first_row_or_column_csv(self):
        d = self.tmp()
        with open(d / "x.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Report title"]); w.writerow([]); w.writerow(["Region", "Sales"])
            for i in range(10):
                w.writerow([["N", "S"][i % 2], 100 + i])
        scan = scan_file(d / "x.csv")
        self.assertEqual(scan.primary[0].header, ["Region", "Sales"])
        self.assertEqual(len(scan.primary[0].rows), 10)

    def test_total_row_and_repeated_header_removed(self):
        d = self.tmp()
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["Region", "Sales"])
        for i in range(6):
            ws.append([f"R{i % 3}", 10 * (i + 1)])
        ws.append(["Region", "Sales"])
        ws.append(["Total", 210])
        wb.save(d / "t.xlsx")
        scan = scan_file(d / "t.xlsx")
        self.assertEqual(len(scan.primary[0].rows), 6)
        self.assertTrue(any("Total" in m for m in scan.primary[0].dropped) and any("repeated header" in m for m in scan.primary[0].dropped))

    def test_same_header_different_rows_are_stacked(self):
        d = self.tmp()
        wb = openpyxl.Workbook()
        for name, base in (("Jan", 0), ("Feb", 100)):
            ws = wb.create_sheet(name)
            ws.append(["Item", "Units"])
            for i in range(5):
                ws.append([f"I{i}", base + i])
        del wb["Sheet"]
        wb.save(d / "m.xlsx")
        ds = build_dataset(scan_file(d / "m.xlsx"))
        self.assertEqual(len(ds.df), 10)
        self.assertIn("Source Sheet", ds.df.columns)

    def test_errors_are_friendly(self):
        d = self.tmp()
        (d / "e.csv").write_text("just one lonely line\n")
        with self.assertRaises(SmartError):
            scan_file(d / "e.csv")
        (d / "old.xls").write_bytes(b"x")
        with self.assertRaises(SmartError) as cm:
            scan_file(d / "old.xls")
        self.assertIn(".xlsx", str(cm.exception))

    def test_hidden_sheet_skipped(self):
        d = self.tmp()
        make_exam_workbook(d / "h.xlsx")
        wb = openpyxl.load_workbook(d / "h.xlsx")
        wb["Solution"].sheet_state = "hidden"
        wb.save(d / "h.xlsx")
        scan = scan_file(d / "h.xlsx")
        self.assertIn("Solution", scan.hidden_sheets)


class Cleaning(Base):
    def test_exam_cleaning(self):
        _, ds, _ = self.exam_ctx()
        df = ds.df
        self.assertTrue(pd.api.types.is_float_dtype(df["Price (USD)"]))
        self.assertEqual(ds.col("Price (USD)").prefix, "$")
        self.assertEqual(ds.col("Year").role, "year")
        self.assertEqual(ds.col("Color").role, "category")
        self.assertFalse(any(v != v.strip() for v in df["Color"].dropna()))        # spaces trimmed -> no 'Blue ' vs 'Blue'
        self.assertIn("Company", df.columns)
        self.assertIn("Country", df.columns)
        self.assertEqual(df["Company"].iloc[0], df["Car"].iloc[0].split(" ")[0])
        self.assertEqual(df["Country"].isna().sum(), 0)
        kinds = {l["kind"] for l in ds.log}
        self.assertTrue({"currency", "trim", "derive", "join"} <= kinds)

    def test_unmatched_lookup_values_reported_not_hidden(self):
        rows = exam_rows()
        rows[0][0] = "Zonda X1"
        _, ds, _ = self.exam_ctx(rows=rows)
        self.assertTrue(any("Zonda" in l["message"] and "No match" in l["message"] for l in ds.log))
        self.assertTrue(pd.isna(ds.df["Country"].iloc[0]))

    def test_case_variants_merged_and_logged(self):
        d = self.tmp()
        with open(d / "c.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["Region", "Sales"])
            for i, r in enumerate(["North", "north", "NORTH", "South", "South", "East", "East", "East", "West", "West"]):
                w.writerow([r, i * 10])
        ds = build_dataset(scan_file(d / "c.csv"))
        self.assertEqual(sorted(ds.df["Region"].unique()), ["East", "North", "South", "West"])
        self.assertTrue(any(l["kind"] == "case" for l in ds.log))

    def test_unreadable_numbers_blanked_and_logged(self):
        d = self.tmp()
        with open(d / "n.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["Item", "Cost"])
            for i in range(30):
                w.writerow([f"i{i}", "N/A?" if i == 5 else f"{1000 + i},50" if False else str(1000 + i)])
        ds = build_dataset(scan_file(d / "n.csv"))
        self.assertEqual(ds.col("Cost").role, "measure")
        self.assertTrue(ds.df["Cost"].isna().sum() == 1)

    def test_ambiguous_dates_are_not_guessed(self):
        d = self.tmp()
        with open(d / "d.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["When", "Value"])
            for i in range(10):
                w.writerow([f"0{1 + i % 8}/0{2 + i % 7}/2025", i])
        ds = build_dataset(scan_file(d / "d.csv"))
        self.assertNotEqual(ds.col("When").role, "date")
        self.assertTrue(any("ambiguous" in l["message"] for l in ds.log))

    def test_personal_columns_detected(self):
        d = self.tmp()
        with open(d / "p.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["Full Name", "Phone", "Sex", "Age", "Ward"])
            for i in range(12):
                w.writerow([f"Person {i}", f"07000000{i:02d}", ["F", "M"][i % 2], 20 + i, ["A", "B", "C"][i % 3]])
        ds = build_dataset(scan_file(d / "p.csv"))
        self.assertEqual(ds.col("Full Name").pii, "hash")
        self.assertEqual(ds.col("Phone").pii, "mask")
        self.assertIsNone(ds.col("Sex").pii)


class Analysis(Base):
    def test_all_internal_checks_pass_and_insights_exist(self):
        _, ds, _ = self.exam_ctx()
        A = analyze(ds)
        self.assertTrue(A.checks and all(c.passed for c in A.checks), [c.name for c in A.checks if not c.passed])
        self.assertTrue(A.frequencies and A.groups and A.correlations)
        self.assertTrue(any("Most common" in i for i in A.insights))
        self.assertTrue(all(abs(c["r"]) <= 1 for c in A.correlations))


class Questions(Base):
    def test_the_four_exam_questions_match_independent_ground_truth(self):
        rows = exam_rows(seed=3)
        t = truth(rows)
        _, ds, ctx = self.exam_ctx(rows=rows)
        qs = ["What's the most popular car manufacturer?", "What's the most popular manufacturing country? (Refer to the 'Manufacturing country' sheet)",
              "What's the least popular color for cars?", "What's the average price per owner?"]
        a1, a2, a3, a4 = [ask(q, ctx) for q in qs]
        for a in (a1, a2, a3, a4):
            self.assertTrue(a.ok and a.verified, a.headline)
        top = max(t["brand"].values())
        self.assertEqual(sorted(a1.headline.split(", ")), sorted(k for k, v in t["brand"].items() if v == top))
        top = max(t["country"].values())
        self.assertEqual(sorted(a2.headline.split(", ")), sorted(k for k, v in t["country"].items() if v == top))
        low = min(t["color"].values())
        self.assertEqual(sorted(a3.headline.split(", ")), sorted(k for k, v in t["color"].items() if v == low))
        got = {r[0]: r[1] for r in a4.table["rows"]}
        self.assertEqual(set(got), set(t["avg_owner"]))
        for k, v in t["avg_owner"].items():
            self.assertAlmostEqual(got[k], v, places=6)

    def test_whitespace_trap_least_popular_color_is_after_trimming(self):
        rows = exam_rows()
        _, ds, ctx = self.exam_ctx(rows=rows)
        a = ask("least popular color", ctx)
        counts = Counter(r[3].strip() for r in rows)
        self.assertEqual(a.table["rows"][0][1], min(counts.values()))
        self.assertFalse(any(r[0] != r[0].strip() for r in a.table["rows"]))

    def test_variety_of_question_styles(self):
        rows = exam_rows(seed=5)
        _, ds, ctx = self.exam_ctx(rows=rows)
        prices = [float(r[6].strip("$")) for r in rows]
        a = ask("What is the average price?", ctx)
        self.assertAlmostEqual(a.table["rows"][0][1], sum(prices) / len(prices), places=6)
        self.assertEqual(ask("How many cars are there?", ctx).headline, str(len(rows)))
        self.assertEqual(ask("How many automatic cars are there?", ctx).headline, str(sum(1 for r in rows if r[4] == "Automatic")))
        a = ask("What is the most expensive car?", ctx)
        best = max(rows, key=lambda r: float(r[6].strip("$")))
        self.assertIn(best[0], a.headline)
        a = ask("average price of cars with more than 100000 km", ctx)
        sel = [float(r[6].strip("$")) for r in rows if r[2] > 100000]
        self.assertAlmostEqual(a.table["rows"][0][1], sum(sel) / len(sel), places=6)
        a = ask("What percentage of cars are automatic?", ctx)
        self.assertEqual(a.headline, f"{sum(1 for r in rows if r[4] == 'Automatic') / len(rows):.1%}")
        a = ask("how many different colors are there?", ctx)
        self.assertEqual(a.headline, str(len({r[3].strip() for r in rows})))
        a = ask("average price by trasmission", ctx)                      # typo
        self.assertTrue(a.ok and {r[0] for r in a.table["rows"]} == {"Automatic", "Manual"})
        a = ask("Is price related to km driven?", ctx)
        self.assertTrue(a.ok and a.table["columns"][2] == "Correlation (r)")
        a = ask("top 3 manufacturers", ctx)
        self.assertEqual(len(a.headline.split(", ")), 3)
        a = ask("average price per year", ctx)
        self.assertTrue(a.ok)

    def test_oldest_is_smallest_year_and_newest_is_largest(self):
        rows = exam_rows(seed=2)
        rows[1][1], rows[2][1] = 2005, 2030                   # one clear oldest and one clear newest
        _, ds, ctx = self.exam_ctx(rows=rows)
        old = ask("Which is the oldest car?", ctx)
        new = ask("Which is the newest car?", ctx)
        self.assertEqual(str(min(r[1] for r in rows)), old.headline.split("(")[-1].strip(")"))
        self.assertEqual(str(max(r[1] for r in rows)), new.headline.split("(")[-1].strip(")"))

    def test_ties_are_reported_not_hidden(self):
        d = self.tmp()
        with open(d / "t.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["Fruit", "Weight"])
            for f in ["apple"] * 3 + ["pear"] * 3 + ["fig"] * 2 + ["kiwi"] * 2 + ["plum"] * 2:
                w.writerow([f.title(), 10])
        ctx = context_from_dataset(build_dataset(scan_file(d / "t.csv")))
        a = ask("most popular fruit", ctx)
        self.assertEqual(sorted(a.headline.split(", ")), ["Apple", "Pear"])
        self.assertIn("tie", a.detail.lower())

    def test_unknown_question_gets_helpful_message_not_a_guess(self):
        _, ds, ctx = self.exam_ctx()
        a = ask("What is the weather like in Nairobi?", ctx)
        self.assertFalse(a.ok)
        self.assertIn("columns are", a.detail.lower())
        self.assertFalse(ask("", ctx).ok)

    def test_a_disagreement_between_the_two_calculations_blocks_the_answer(self):
        _, ds, ctx = self.exam_ctx()
        with mock.patch.object(smart_qa, "py_mean", lambda xs: 123456.0):
            a = ask("average price per owner", ctx)
        self.assertFalse(a.ok)
        self.assertIn("disagreed", a.headline)

    def test_personal_data_columns_cannot_be_asked_about(self):
        d = self.tmp()
        with open(d / "p.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["Full Name", "Sex", "Age"])
            for i in range(12):
                w.writerow([f"Person {i}", ["F", "M"][i % 2], 20 + i])
        ctx = context_from_dataset(build_dataset(scan_file(d / "p.csv")))
        a = ask("most popular full name", ctx)
        self.assertNotIn("Person", json.dumps(a.to_dict()))


class AI(Base):
    def fake_response(self, payload):
        class R:
            def __init__(self, data): self._d = data
            def read(self): return json.dumps({"content": [{"type": "text", "text": self._d}]}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return lambda req, timeout=0: R(payload)

    def test_off_by_default(self):
        self.assertFalse(smart_qa.ai_enabled())
        _, ds, ctx = self.exam_ctx()
        with mock.patch.object(smart_qa, "_opener") as op:
            ask("weird thing nobody understands", ctx)
        op.assert_not_called()

    def test_ai_translation_is_validated_and_numbers_still_computed_locally(self):
        _, ds, ctx = self.exam_ctx()
        env = {"SWA_AI_ENABLED": "1", "ANTHROPIC_API_KEY": "k"}
        good = json.dumps({"intent": "agg_by", "measure": "Price (USD)", "agg": "max", "by": "Owner", "order": "desc", "n": 1})
        with mock.patch.dict("os.environ", env), mock.patch.object(smart_qa, "_opener", lambda: self.fake_response(good)):
            a = ask("blorp blarp zzz", ctx)
        self.assertTrue(a.ok and a.verified and a.method == "ai")
        self.assertTrue(any("AI model translated" in n for n in a.notes))

    def test_ai_nonsense_columns_ignored_and_errors_handled(self):
        _, ds, ctx = self.exam_ctx()
        env = {"SWA_AI_ENABLED": "1", "ANTHROPIC_API_KEY": "k"}
        for payload in (json.dumps({"intent": "agg", "measure": "Salary", "agg": "mean"}), "not json at all", json.dumps({"intent": "drop_table"}),
                        json.dumps({"intent": "none", "why": "no"})):
            with mock.patch.dict("os.environ", env), mock.patch.object(smart_qa, "_opener", lambda p=payload: self.fake_response(p)):
                self.assertFalse(ask("blorp blarp zzz", ctx).ok)

        def boom():
            def f(req, timeout=0):
                raise OSError("network down")
            return f
        with mock.patch.dict("os.environ", env), mock.patch.object(smart_qa, "_opener", boom):
            self.assertFalse(ask("blorp blarp zzz", ctx).ok)

    def test_ai_never_receives_rows_or_personal_data(self):
        d = self.tmp()
        with open(d / "p.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["Full Name", "Phone", "Sex", "Score"])
            for i in range(12):
                w.writerow([f"Secret Person {i}", f"0700000{i:03d}", ["F", "M"][i % 2], 50 + i * 3])
        ctx = context_from_dataset(build_dataset(scan_file(d / "p.csv")))
        sent = {}

        def capture():
            def f(req, timeout=0):
                sent["body"] = req.data.decode()
                raise OSError("stop")
            return f
        with mock.patch.dict("os.environ", {"SWA_AI_ENABLED": "1", "ANTHROPIC_API_KEY": "k"}), mock.patch.object(smart_qa, "_opener", capture):
            ask("blorp blarp zzz", ctx)
        body = sent["body"]
        self.assertNotIn("Secret Person", body)
        self.assertNotIn("0700000", body)
        self.assertIn("Score", body)


class EndToEnd(Base):
    def test_run_smart_full_pipeline_with_answers(self):
        d = self.tmp()
        make_exam_workbook(d / "exam.xlsx")
        res = run_smart(d / "exam.xlsx", d / "out", "My Exam", ["How many automatic cars are there?"], display_name="exam.xlsx")
        self.assertTrue(res.ok, res.message)
        a = json.loads((res.out_dir / "audit.json").read_text())
        self.assertEqual(a["rows"], {"read": 40, "analysed": 40, "quarantined": 0, "blank": 0})
        self.assertEqual(a["dataset_name"], "My Exam")
        self.assertTrue(all(c["passed"] for c in a["checks"]), [c for c in a["checks"] if not c["passed"]])
        self.assertEqual(a["smart"]["questions"], 5)
        self.assertEqual(a["smart"]["answers_verified"], 5)
        wb = openpyxl.load_workbook(res.out_dir / "report.xlsx", data_only=True)
        for sheet in ("Answers", "Insights", "Column Profile", "Frequencies", "Compare Groups", "Correlations", "Cleaning Log", "Clean Data", "Checks"):
            self.assertIn(sheet, wb.sheetnames)
        text = " ".join(str(c.value) for row in wb["Answers"].iter_rows() for c in row if c.value is not None)
        self.assertIn("calculated twice", text)
        html = (res.out_dir / "report.html").read_text()
        self.assertIn("Answers to your questions", html)
        self.assertIn("swasamson.site", html)

    def test_later_questions_use_saved_results(self):
        d = self.tmp()
        make_exam_workbook(d / "exam.xlsx")
        res = run_smart(d / "exam.xlsx", d / "out", "T", [])
        ctx = load_qa_context(res.out_dir)
        self.assertEqual(len(ctx.df), 40)
        a = ask("average price per owner", ctx)
        _, ds, ctx2 = self.exam_ctx()
        b = ask("average price per owner", ctx2)
        self.assertEqual({r[0]: round(r[1], 6) for r in a.table["rows"]}, {r[0]: round(r[1], 6) for r in b.table["rows"]})

    def test_personal_data_hidden_in_every_output(self):
        d = self.tmp()
        with open(d / "p.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["Full Name", "Phone", "Sex", "Age", "Ward"])
            for i in range(20):
                w.writerow([f"Zelda Secret{i}", f"07123456{i:02d}", ["Female", "Male"][i % 2], 20 + i, ["A", "B"][i % 2]])
        res = run_smart(d / "p.csv", d / "out", "People", ["What is the most popular Ward?"], salt="s3cret")
        self.assertTrue(res.ok, res.message)
        for f in res.out_dir.iterdir():
            if f.suffix in (".csv", ".json", ".html", ".log"):
                t = f.read_text(encoding="utf-8-sig")
                self.assertNotIn("Zelda", t, f.name)
                self.assertNotIn("07123456", t, f.name)
        wb = openpyxl.load_workbook(res.out_dir / "report.xlsx")
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for v in row:
                    self.assertFalse(isinstance(v, str) and ("Zelda" in v or "07123456" in v), ws.title)

    def test_clean_failure_for_unusable_file_and_big_file_cap(self):
        d = self.tmp()
        (d / "bad.csv").write_text("hello\n")
        res = run_smart(d / "bad.csv", d / "out", "T", [])
        self.assertFalse(res.ok)
        self.assertTrue((res.out_dir / "ERROR.txt").exists())
        with mock.patch("swa_data_tool.smart.MAX_SMART_ROWS", 5):
            make_exam_workbook(d / "e.xlsx")
            res = run_smart(d / "e.xlsx", d / "out2", "T", [])
        self.assertFalse(res.ok)
        self.assertIn("supports up to", res.message)

    def test_formula_injection_in_text_cells_stays_text(self):
        d = self.tmp()
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["Item", "Units"])
        for i in range(12):
            ws.append(['=HYPERLINK("http://evil","x")' if i == 0 else f"I{i % 3}", i])
        wb.save(d / "i.xlsx")
        res = run_smart(d / "i.xlsx", d / "out", "T", [])
        self.assertTrue(res.ok, res.message)
        wb2 = openpyxl.load_workbook(res.out_dir / "report.xlsx")
        for ws in wb2.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if c.data_type == "f":
                        self.assertNotIn("evil", str(c.value))


@unittest.skipUnless(REAL_EXAM.exists(), "the real exam file is not available here")
class RealExamFile(unittest.TestCase):
    def test_real_file_answers(self):
        scan = scan_file(REAL_EXAM)
        ds = build_dataset(scan)
        ctx = context_from_dataset(ds)
        self.assertEqual(len(ds.df), 18)
        self.assertEqual(len(scan.questions), 4)
        a1, a2, a3, a4 = [ask(q, ctx) for q in scan.questions]
        self.assertEqual(a1.headline, "BMW")
        self.assertEqual(a2.headline, "Germany")
        self.assertEqual(a3.headline, "Yellow")
        self.assertEqual(len(a4.table["rows"]), 4)
        self.assertTrue(all(a.verified for a in (a1, a2, a3, a4)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
