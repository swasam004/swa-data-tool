import csv
import json
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

from swa_data_tool.config import ConfigError, load_config  # noqa: E402
from swa_data_tool.pipeline import STATUS_FAIL, process_file  # noqa: E402

NGO_CFG = """
dataset_name: NGO
privacy_salt: "test-secret"
folders: {inbox: inbox, outputs: out}
columns:
  - {name: ID, source: "grp/id", type: text, required: true}
  - {name: Name, source: "grp/name", type: text, pii: hash}
  - {name: Phone, type: text, pii: mask}
  - {name: Sex, type: category, required: true, case: title, allowed: [Female, Male], mapping: {f: Female, m: Male}}
  - {name: Age, type: number, integer: true, required: true, min: 0, bands: {name: Age Group, edges: [0, 5, 15, 25]}}
  - {name: Done, type: category, mapping: {"yes": "Yes", "no": "No"}, allowed: ["Yes", "No"]}
  - {name: Date, type: date, required: true, date_formats: ["%Y-%m-%d"]}
  - {name: Income, type: multiselect, options: [farming, trade, none]}
pivots:
  - {name: People by Sex, rows: Sex, values: ID, agg: count_unique}
  - {name: Records by Age Group and Sex, rows: Age Group, columns: Sex, agg: count_rows, percent_of: row}
  - {name: Farming by Sex, rows: Sex, values: "Income: farming", agg: sum}
indicators:
  - name: Done rate among youth
    type: percent
    where: [{column: Done, equals: "Yes"}]
    among: [{column: Age Group, in: ["5-14", "15-24"]}]
    unique_by: ID
    disaggregate_by: [Sex]
  - name: Records
    type: count
    disaggregate_by: [Sex, Age Group]
"""

HEADER = ["grp/id", "grp/name", "Phone", "Sex", "Age", "Done", "Date", "Income"]


def project(cfg_text=NGO_CFG):
    d = Path(tempfile.mkdtemp())
    (d / "c.yaml").write_text(cfg_text)
    return d, load_config(d / "c.yaml")


def write_rows(path, rows, header=HEADER):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(header); w.writerows(rows)


ROWS = [
    ["P1", "Alice Wanjiru", "0712345678", "female", "4", "yes", "2025-01-10", "farming"],
    ["P1", "Alice Wanjiru", "0712345678", "female", "4", "no", "2025-02-10", "farming trade"],
    ["P2", "Bob Otieno", "0722222222", "m", "15", "yes", "2025-03-10T10:22:11+03:00", "trade"],
    ["P3", "Cathy Njeri", "0733333333", "Female", "24", "yes", "2025-03-11", "none"],
    ["P4", "Dan Kamau", "0744444444", "male", "25", "no", "2025-04-01", ""],
    ["P5", "Eve Atieno", "0755555555", "female", "5", "yes", "2025-04-02", "farming"],
    ["P6", "Fred Mutua", "0766666666", "male", "14", "no", "2025-04-03", "trade"],
    ["P7", "Gina Ouma", "0777777777", "female", "0", "yes", "2025-04-04", "farming"],
    ["P8", "Hank Odhiambo", "0788888888", "male", "30", "yes", "2025-05-05", "trade"],
    ["P9", "Ivy Achieng", "0799999999", "female", "16", "no", "2025-05-06", "none"],
    ["P10", "Jack Kiprop", "0701111111", "male", "20", "yes", "2025-05-07", "farming"],
    ["P11", "Kim Mwangi", "0702222222", "female", "9", "yes", "2025-05-08", "trade"],
]


class Ngo(unittest.TestCase):
    def setUp(self):
        self.dir, self.cfg = project()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def run_rows(self, rows):
        f = self.dir / "in.csv"; write_rows(f, rows)
        return process_file(f, self.cfg)

    def audit(self, res):
        return json.loads((res.out_dir / "audit.json").read_text())

    def test_bands_exact_boundaries(self):
        res = self.run_rows(ROWS)
        df = pd.read_csv(res.out_dir / "clean_data.csv", dtype=str)
        got = dict(zip(df["Age"], df["Age Group"]))
        self.assertEqual(got, {"4": "0-4", "15": "15-24", "24": "15-24", "25": "25+", "5": "5-14", "14": "5-14",
                               "0": "0-4", "30": "25+", "16": "15-24", "20": "15-24", "9": "5-14"})

    def test_kobo_alias_timestamp_with_timezone_and_mapping(self):
        res = self.run_rows(ROWS)
        df = pd.read_csv(res.out_dir / "clean_data.csv", dtype=str)
        row = df[df["Beneficiary ID" if "Beneficiary ID" in df else "ID"] == "P2"].iloc[0]
        self.assertEqual(row["Sex"], "Male")                 # 'm' mapped
        self.assertEqual(row["Date"], "2025-03-10")          # timestamp with +03:00 accepted, date kept as written

    def test_unique_people_not_records_and_total_not_sum_of_rows(self):
        res = self.run_rows(ROWS)
        a = self.audit(res)
        self.assertEqual(a["pivot_grand_totals"]["People by Sex"], 11.0)   # 12 records, P1 twice
        wb = openpyxl.load_workbook(res.out_dir / "report.xlsx", data_only=True)
        ws = wb["People by Sex"]
        vals = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(5, 8)}
        self.assertEqual(vals["Female"], 6.0); self.assertEqual(vals["Male"], 5.0); self.assertEqual(vals["Total"], 11.0)

    def test_percent_of_row_sums_to_100(self):
        res = self.run_rows(ROWS)
        a = self.audit(res)
        ok = [c for c in a["checks"] if "percentage table" in c["name"]]
        self.assertTrue(ok and all(c["passed"] for c in ok))

    def test_multiselect_columns_and_unknown_option(self):
        rows = [r[:] for r in ROWS] + [["P12", "Lee", "0703333333", "male", "22", "yes", "2025-06-01", "farming fishing"]]
        res = self.run_rows(rows)
        a = self.audit(res)
        self.assertEqual(a["rows"]["quarantined"], 1)
        df = pd.read_csv(res.out_dir / "clean_data.csv", dtype=str)
        self.assertEqual(df["Income: farming"].tolist().count("1"), 5)
        blank = df[df["ID"] == "P4"].iloc[0]
        self.assertTrue(pd.isna(blank["Income: farming"]))            # unanswered -> blank, not 0

    def test_indicator_ground_truth(self):
        res = self.run_rows(ROWS)
        a = self.audit(res)
        # youth = ages 5-24 -> P1(4: no), P2(15), P3(24), P5(5), P6(14), P7(0 -> 0-4 no), P9(16), P10(20), P11(9)
        youth = {"P2": True, "P3": True, "P5": True, "P6": False, "P9": False, "P10": True, "P11": True}
        num = sum(1 for v in youth.values() if v); den = len(youth)
        t = a["indicator_totals"]["Done rate among youth"]
        self.assertEqual((t["n"], t["d"]), (float(num), float(den)))
        self.assertAlmostEqual(t["value"], num / den)
        self.assertEqual(a["indicator_totals"]["Records"]["n"], 12.0)

    def test_failed_checks_none_and_status(self):
        res = self.run_rows(ROWS)
        self.assertNotEqual(res.status, STATUS_FAIL)
        self.assertTrue(all(c["passed"] for c in self.audit(res)["checks"]))

    def test_no_personal_data_in_any_output(self):
        rows = [r[:] for r in ROWS] + [["P13", "Zed Secret", "0799887766", "unknown", "22", "yes", "2025-06-01", "farming"]]  # quarantined row
        res = self.run_rows(rows)
        secrets = {r[1] for r in rows} | {r[2] for r in rows}
        for f in res.out_dir.iterdir():
            if f.suffix in (".csv", ".html", ".json", ".log", ".txt"):
                text = f.read_text(encoding="utf-8-sig")
                for sct in secrets:
                    self.assertNotIn(sct, text, f"{sct} leaked into {f.name}")
        wb = openpyxl.load_workbook(res.out_dir / "report.xlsx")
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for v in row:
                    if isinstance(v, str):
                        for sct in secrets:
                            self.assertNotIn(sct, v)
        # same person -> same code (so people can still be counted); different people differ
        df = pd.read_csv(res.out_dir / "clean_data.csv", dtype=str)
        p1 = df[df["ID"] == "P1"]["Name"].unique()
        self.assertEqual(len(p1), 1)
        self.assertEqual(df["Name"].nunique(), df["ID"].nunique())
        self.assertTrue(df[df["ID"] == "P2"]["Phone"].iloc[0].endswith("222"))

    def test_pii_duplicate_reason_does_not_echo_value(self):
        cfg_text = NGO_CFG.replace("{name: Name, source: \"grp/name\", type: text, pii: hash}",
                                   "{name: Name, source: \"grp/name\", type: text, pii: hash, unique: true}")
        d, cfg = project(cfg_text); self.addCleanup(shutil.rmtree, d, True)
        f = d / "in.csv"; write_rows(f, ROWS)
        res = process_file(f, cfg)           # P1 appears twice with the same name -> second quarantined
        q = (res.out_dir / "quarantined_rows.csv").read_text(encoding="utf-8-sig")
        self.assertIn("duplicate of an earlier row", q)
        self.assertNotIn("Alice", q)

    def test_indicator_filter_typo_warns(self):
        d, cfg = project(NGO_CFG.replace("in: [\"5-14\", \"15-24\"]", "in: [\"5-14\", \"15-25\"]"))
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "in.csv"; write_rows(f, ROWS)
        res = process_file(f, cfg)
        self.assertTrue(any("15-25" in w for w in json.loads((res.out_dir / "audit.json").read_text())["warnings"]))


class ConfigGuards(unittest.TestCase):
    def test_unquoted_yes_no_rejected(self):
        with self.assertRaises(ConfigError):
            project(NGO_CFG.replace('mapping: {"yes": "Yes", "no": "No"}', "mapping: {yes: Yes, no: No}"))

    def test_pii_requires_text_type(self):
        with self.assertRaises(ConfigError):
            project(NGO_CFG.replace("{name: Sex, type: category", "{name: Sex, pii: hash, type: category"))

    def test_pii_column_cannot_be_grouped_or_filtered(self):
        with self.assertRaises(ConfigError):
            project(NGO_CFG + "\n  - name: Bad\n    type: count\n    where: [{column: Name, equals: x}]\n")

    def test_filter_value_must_be_allowed(self):
        with self.assertRaises(ConfigError):
            project(NGO_CFG.replace('equals: "Yes"}]\n    among', 'equals: "Maybe"}]\n    among'))

    def test_bands_need_increasing_edges(self):
        with self.assertRaises(ConfigError):
            project(NGO_CFG.replace("edges: [0, 5, 15, 25]", "edges: [0, 15, 5]"))


class NgoSample(unittest.TestCase):
    """Independent ground truth for the shipped NGO sample, using only the csv module."""

    def test_sample(self):
        cfg = load_config(ROOT / "swa_data_tool" / "templates" / "config_ngo.yaml")
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        cfg["outputs"] = str(tmp)
        src = ROOT / "swa_data_tool" / "templates" / "ngo_beneficiary_register.csv"
        res = process_file(src, cfg)
        a = json.loads((res.out_dir / "audit.json").read_text())
        self.assertTrue(all(c["passed"] for c in a["checks"]), [c for c in a["checks"] if not c["passed"]])

        seen, recs = set(), []
        with open(src, newline="") as fh:
            for r in csv.DictReader(fh):
                if not any(r.values()):
                    continue
                if r["_id"] in seen:
                    continue
                sex = {"f": "Female", "m": "Male"}.get(r["group_demo/sex"].lower(), r["group_demo/sex"].title())
                if sex not in ("Female", "Male"):
                    continue
                if not re.fullmatch(r"\d+", r["group_demo/age"]) or int(r["group_demo/age"]) > 100:
                    continue
                if not re.fullmatch(r"\d+", r["sessions_attended"]) and r["sessions_attended"] != "":
                    continue
                if not re.fullmatch(r"2025-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T.*", r["_submission_time"]):
                    continue
                if set(r["income_sources"].split()) - {"farming", "small_business", "casual_labour", "remittances", "none"}:
                    continue
                seen.add(r["_id"])
                inc = r["monthly_income_kes"].replace("KES", "").replace(",", "").strip()
                recs.append({"code": r["group_reg/beneficiary_code"], "age": int(r["group_demo/age"]), "sex": sex,
                             "done": r["completed_training"].strip().lower() in ("yes", "y"),
                             "done_blank": r["completed_training"].strip() == "",
                             "casual": "casual_labour" in r["income_sources"].split(), "income": float(inc)})
        self.assertEqual(a["rows"]["analysed"], len(recs))
        t = a["indicator_totals"]
        self.assertEqual(t["Total participations"]["n"], len(recs))
        self.assertEqual(t["Unique beneficiaries reached"]["n"], len({r["code"] for r in recs}))
        youth = [r for r in recs if 15 <= r["age"] <= 24]
        den = len({r["code"] for r in youth})
        num = len({r["code"] for r in youth if r["done"]})
        self.assertEqual((t["Youth (15-24) training completion rate"]["n"], t["Youth (15-24) training completion rate"]["d"]), (num, den))
        self.assertEqual(t["Records reporting casual labour income"]["n"], sum(r["casual"] for r in recs))
        self.assertAlmostEqual(t["Average monthly income (KES)"]["value"], sum(r["income"] for r in recs) / len(recs), places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
