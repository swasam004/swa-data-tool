import csv
import json
import math
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from swa_data_tool.cleaning import parse_number  # noqa: E402
from swa_data_tool.config import ConfigError, load_config  # noqa: E402
from swa_data_tool.pipeline import STATUS_FAIL, STATUS_INPUT, STATUS_OK, STATUS_WARN, process_file  # noqa: E402
from swa_data_tool.watcher import scan_once  # noqa: E402

SYMS = ["KES", "$"]

BASE_CFG = """
dataset_name: T
folders: {inbox: inbox, outputs: out}
columns:
  - {name: ID, type: text, required: true, unique: true}
  - {name: Date, type: date, required: true, date_formats: ["%d/%m/%Y"]}
  - {name: Group, type: category}
  - {name: Amount, type: number, required: true}
pivots:
  - {name: By Group, rows: Group, values: Amount, agg: sum}
  - {name: Avg By Group, rows: Group, values: Amount, agg: mean}
  - {name: By Month x Group, rows: Date (Month), columns: Group, values: Amount, agg: sum}
"""


def make_project(cfg_text=BASE_CFG):
    d = Path(tempfile.mkdtemp())
    (d / "config.yaml").write_text(cfg_text)
    return d, load_config(d / "config.yaml")


def write_csv(path, rows, header=("ID", "Date", "Group", "Amount")):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


class NumberParsing(unittest.TestCase):
    def test_valid(self):
        for text, want in [("1,200.50", 1200.5), ("KES 1,200.50", 1200.5), ("(500)", -500.0), ("-500", -500.0),
                           ("500-", -500.0), ("$ -20", -20.0), ("1e3", 1000.0), (".5", 0.5), ("0", 0.0)]:
            self.assertEqual(parse_number(text, ".", ",", SYMS), (want, None), text)

    def test_invalid(self):
        for text in ["abc", "nan", "inf", "-inf", "1,2,3", "1,5", "--5", "1.2.3", "", "12,34.00", "1 2"]:
            v, why = parse_number(text, ".", ",", SYMS)
            self.assertIsNone(v, text)
            self.assertTrue(why, text)

    def test_decimal_comma(self):
        self.assertEqual(parse_number("1.234,56", ",", ".", SYMS), (1234.56, None))

    def test_no_negative_zero(self):
        v, _ = parse_number("-0", ".", ",", SYMS)
        self.assertEqual(math.copysign(1, v), 1.0)


class ConfigStrictness(unittest.TestCase):
    def test_typo_rejected(self):
        with self.assertRaises(ConfigError):
            d, _ = make_project(BASE_CFG.replace("required: true, unique: true", "reqired: true, unique: true"))

    def test_date_needs_format(self):
        with self.assertRaises(ConfigError):
            make_project(BASE_CFG.replace(', date_formats: ["%d/%m/%Y"]', ""))

    def test_pivot_on_unknown_column(self):
        with self.assertRaises(ConfigError):
            make_project(BASE_CFG.replace("rows: Group, values: Amount, agg: sum", "rows: Nope, values: Amount, agg: sum"))

    def test_sum_of_text_rejected(self):
        with self.assertRaises(ConfigError):
            make_project(BASE_CFG.replace("rows: Group, values: Amount, agg: sum", "rows: Group, values: ID, agg: sum"))


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir, self.cfg = make_project()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def run_rows(self, rows, **kw):
        f = self.dir / "in.csv"
        write_csv(f, rows, **kw)
        return process_file(f, self.cfg)

    def test_ground_truth_and_accounting(self):
        rows = [["A1", "01/01/2025", "x", "100.10"], ["A2", "15/01/2025", "y", "200.20"],
                ["A3", "03/02/2025", "x", "KES 300.30"], ["A4", "04/02/2025", "", "50"],
                ["A5", "05/02/2025", "y", "oops"],            # bad number
                ["A1", "06/02/2025", "x", "1"],               # duplicate id
                ["A6", "31/02/2025", "x", "1"],               # impossible date
                ["", "", "", ""],                             # blank
                ["A7", "07/02/2025", "y", "(10)"]]            # negative via brackets (allowed here)
        res = self.run_rows(rows)
        self.assertEqual(res.status, STATUS_FAIL)   # 3 of 9 rows quarantined > 10% limit -> must FAIL
        audit = json.loads((res.out_dir / "audit.json").read_text())
        self.assertEqual(audit["rows"], {"read": 9, "analysed": 5, "quarantined": 3, "blank": 1})
        self.assertAlmostEqual(audit["pivot_grand_totals"]["By Group"], 100.10 + 200.20 + 300.30 + 50 - 10, places=9)
        # groups, including the blank category
        wb = openpyxl.load_workbook(res.out_dir / "report.xlsx", data_only=True)
        ws = wb["By Group"]
        got = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(5, 9)}
        self.assertAlmostEqual(got["x"], 400.40); self.assertAlmostEqual(got["y"], 190.20)
        self.assertAlmostEqual(got["(Missing)"], 50.0); self.assertEqual(got["Total"], audit["pivot_grand_totals"]["By Group"])

    def test_clean_run_passes(self):
        res = self.run_rows([["A1", "01/01/2025", "x", "10"], ["A2", "01/02/2025", "y", "20"]])
        self.assertEqual(res.status, STATUS_OK)

    def test_mean_total_is_not_mean_of_means(self):
        res = self.run_rows([["1", "01/01/2025", "a", "10"], ["2", "01/01/2025", "a", "10"],
                             ["3", "01/01/2025", "a", "10"], ["4", "01/01/2025", "b", "100"]])
        audit = json.loads((res.out_dir / "audit.json").read_text())
        self.assertAlmostEqual(audit["pivot_grand_totals"]["Avg By Group"], 32.5)   # not (10+100)/2 = 55

    def test_missing_required_column_fails_cleanly(self):
        res = self.run_rows([["A1", "01/01/2025", "x"]], header=("ID", "Date", "Group"))
        self.assertEqual(res.status, STATUS_INPUT)
        self.assertTrue((res.out_dir / "ERROR.txt").exists())
        self.assertFalse((res.out_dir / "report.xlsx").exists())

    def test_formula_injection_is_neutralised(self):
        res = self.run_rows([["A1", "01/01/2025", '=HYPERLINK("http://evil","x")', "10"], ["A2", "01/01/2025", "y", "5"]])
        wb = openpyxl.load_workbook(res.out_dir / "report.xlsx")
        ws = wb["Clean Data"]
        cell = ws.cell(2, 4)
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(str(cell.value).startswith("=HYPERLINK"))
        csv_text = (res.out_dir / "clean_data.csv").read_text(encoding="utf-8-sig")
        self.assertIn("'=HYPERLINK", csv_text)

    def test_leading_zero_ids_survive(self):
        res = self.run_rows([["007", "01/01/2025", "x", "10"], ["008", "01/01/2025", "y", "5"]])
        df = pd.read_csv(res.out_dir / "clean_data.csv", dtype=str)
        self.assertEqual(df["ID"].tolist(), ["007", "008"])

    def test_excel_input_with_real_dates(self):
        f = self.dir / "in.xlsx"
        pd.DataFrame({"ID": ["A1", "A2"], "Date": pd.to_datetime(["2025-03-05", "2025-04-06"]),
                      "Group": ["x", "y"], "Amount": [10.5, 20.25]}).to_excel(f, index=False)
        res = process_file(f, self.cfg)
        self.assertEqual(res.status, STATUS_OK)
        audit = json.loads((res.out_dir / "audit.json").read_text())
        self.assertAlmostEqual(audit["pivot_grand_totals"]["By Group"], 30.75)

    def test_day_month_not_guessed(self):
        # 03/04/2025 must be 3 April with %d/%m/%Y, never 4 March
        res = self.run_rows([["A1", "03/04/2025", "x", "1"]])
        df = pd.read_csv(res.out_dir / "clean_data.csv", dtype=str)
        self.assertEqual(df["Date"].iloc[0], "2025-04-03")
        self.assertEqual(df["Date (Month)"].iloc[0], "2025-04")

    def test_malformed_csv_row_is_quarantined_not_shifted(self):
        res = self.run_rows([["A1", "01/01/2025", "x", "10"], ["A2", "01/01/2025", "y"],
                             ["A3", "01/01/2025", "x", "30"], ["A4", "01/01/2025", "y", "40"],
                             ["A5", "01/01/2025", "x", "50"], ["A6", "01/01/2025", "y", "60"],
                             ["A7", "01/01/2025", "x", "70"], ["A8", "01/01/2025", "y", "80"],
                             ["A9", "01/01/2025", "x", "90"], ["A10", "01/01/2025", "y", "100"],
                             ["A11", "01/01/2025", "x", "110"], ["A12", "01/01/2025", "y", "120"]])
        audit = json.loads((res.out_dir / "audit.json").read_text())
        self.assertEqual(audit["rows"]["quarantined"], 1)
        self.assertEqual(audit["rows"]["read"], 12)

    def test_unicode_and_comma_in_quotes(self):
        res = self.run_rows([["A1", "01/01/2025", "Nairobi, KE", "10"], ["A2", "01/01/2025", "Mombasa é", "5"]])
        df = pd.read_csv(res.out_dir / "clean_data.csv", dtype=str)
        self.assertEqual(df["Group"].tolist(), ["Nairobi, KE", "Mombasa é"])

    def test_pivot_formulas_present_and_totals_match(self):
        res = self.run_rows([["A1", "01/01/2025", "x", "10.10"], ["A2", "01/02/2025", "y", "20.20"], ["A3", "01/02/2025", "x", "1"]])
        wb = openpyxl.load_workbook(res.out_dir / "report.xlsx")
        self.assertTrue(str(wb["By Group"]["B5"].value).startswith("=SUMPRODUCT("))
        self.assertTrue(str(wb["By Group"]["B7"].value).startswith("=SUM("))


class Watcher(unittest.TestCase):
    def test_scan_moves_files_and_blocks_reprocessing(self):
        d, cfg = make_project()
        self.addCleanup(shutil.rmtree, d, True)
        inbox = Path(cfg["inbox"]); inbox.mkdir()
        rows = [["A1", "01/01/2025", "x", "10"], ["A2", "01/02/2025", "y", "20"]]
        write_csv(inbox / "a.csv", rows)
        out = scan_once(cfg)
        self.assertEqual([r.status for _, r in out], [STATUS_OK])
        self.assertTrue(list((inbox / "_processed").glob("*a.csv")))
        write_csv(inbox / "again.csv", rows)          # identical content, different name
        self.assertEqual(scan_once(cfg), [])
        self.assertTrue(list((inbox / "_duplicates").glob("*again.csv")))
        write_csv(inbox / "bad.csv", [["A1", "01/01/2025", "x"]], header=("ID", "Date", "Group"))
        out = scan_once(cfg)
        self.assertEqual(out[0][1].status, STATUS_INPUT)
        self.assertTrue(list((inbox / "_failed").glob("*bad.csv")))


class SampleData(unittest.TestCase):
    """Independent ground truth for the shipped sample, using only the csv module."""

    def test_sample_totals(self):
        cfg = load_config(ROOT / "swa_data_tool" / "templates" / "config_sales.yaml")
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        cfg["outputs"] = str(tmp)
        res = process_file(ROOT / "swa_data_tool" / "templates" / "sales_sample.csv", cfg)
        self.assertEqual(res.status, STATUS_WARN)
        audit = json.loads((res.out_dir / "audit.json").read_text())

        seen, total, n = set(), 0.0, 0
        with open(ROOT / "swa_data_tool" / "templates" / "sales_sample.csv", newline="") as fh:
            rd = csv.reader(fh); next(rd)
            for r in rd:
                if not r or not any(x.strip() for x in r) or len(r) != 6:
                    continue
                oid, date, region, prod, units, rev = [x.strip() for x in r]
                if not oid or oid in seen or not prod:
                    continue
                if not re.fullmatch(r"(0[1-9]|[12]\d|3[01])/(0[1-9]|1[0-2])/2025", date) or date.startswith("31/02"):
                    continue
                if region.title() not in {"North", "South", "East", "West"}:
                    continue
                if not re.fullmatch(r"\d+", units):
                    continue
                money = rev.replace("KES", "").strip()
                if not re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d+)?|\d+(\.\d+)?", money):
                    continue
                seen.add(oid); total += float(money.replace(",", "")); n += 1
        self.assertEqual(audit["rows"]["analysed"], n)
        self.assertAlmostEqual(audit["pivot_grand_totals"]["Revenue by Region and Product"], total, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
