# SWA Data Analysis Tool

Drop a data file (CSV / Excel / JSON) in a folder → get a **verified** Excel report (pivot tables, charts,
programme indicators), an HTML report, cleaned data, a list of rejected rows and an audit record.
Built for NGO and programme data (KoboToolbox/ODK exports, beneficiary registers, attendance, surveys) and
works equally for sales, finance or any tabular data.

## Run as a website
`swa-tool serve --config config.yaml` starts a browser interface (upload a file → verified report). Deployment on
Render, the advertising slots, visitor privacy and all environment variables are explained in **WEB_GUIDE.md**.

## 1. Install (once)
1. Install Python 3.10+ from python.org (Windows: tick **Add Python to PATH**).
2. Open a terminal in this folder and run:  `pip install .`
   (this installs the tool and the `swa-tool` command; `python -m swa_data_tool ...` also works)

## 2. Try it in 1 minute
```
swa-tool init my_project --template ngo        (or --template sales)
cd my_project
swa-tool run ngo_beneficiary_register.csv --config config.yaml
```
Open the newest folder in `outputs/` → `report.xlsx` → read **Summary** and **Checks** first.

## 3. Use it on your own data
1. Edit `my_project/config.yaml`. Describe each column once (name, type, rules) and the pivots/indicators
   you want. The NGO template explains every setting in comments.
2. `swa-tool check-config -c config.yaml` – validates the file and tells you exactly what to fix.
3. **Background mode:** `swa-tool watch -c config.yaml` (or double-click `start_watcher.bat`).
   Drop files into `inbox/`; each is analysed automatically and moved to `inbox/_processed`
   (`_failed` if it could not be trusted, `_duplicates` if the identical file was done before).
   Run only ONE watcher per inbox. To start at login: Windows Task Scheduler → "At log on".
4. `swa-tool scan -c config.yaml` processes whatever is in the inbox now, then stops.

## What each run produces (`outputs/<date>_<file>/`)
| File | Purpose |
|---|---|
| report.xlsx | Summary, Checks, a sheet per pivot (table + % table + chart), **Indicators**, Summary Stats (incl. multi-select questions), Data Quality (incl. completeness), Clean Data, Quarantined Rows |
| report.html | The same results as one self-contained page you can e-mail |
| clean_data.csv | Exactly the rows analysed (`Source Row` = row in your original file) |
| quarantined_rows.csv | Rejected rows with the reason |
| audit.json, run.log | File fingerprint (SHA-256), configuration fingerprint, counts, every check result |

## NGO features
* **KoboToolbox / ODK exports.** Map long headers such as `group_demo/age` with `source:` (and `aliases:`).
  Kobo timestamps (`2025-03-05T10:22:11`, with time zone) are accepted. `select_multiple` answers
  (space-separated) become a `multiselect` column. In Kobo's export settings use CSV, keep question
  codes ("XML values and headers") and group names in headers; if your CSV uses `;`, set `read: delimiter: ";"`.
* **Personal data protection.** `pii: hash` (salted code – same person → same code, so people can still be
  counted), `pii: mask` (only last 3 characters) or `pii: drop`. Applied to **every** output (Excel, HTML, CSV,
  rejected-row reasons, logs). Set `privacy_salt` to a long secret phrase. The analysis itself uses the real values.
* **Age groups / bands.** `bands: {name: Age Group, edges: [0, 5, 15, 25, 50]}` creates 0-4, 5-14, 15-24, 25-49, 50+
  (lower edge included, upper edge excluded). Values below the first edge are rejected, not guessed.
* **Multi-select questions.** Each option becomes a 0/1 column (`Income Sources: farming`), so you can sum it in
  pivots or use it in indicators. Unknown options are rejected. Unanswered stays blank (not 0).
* **Unique people vs records.** `agg: count_unique` counts distinct IDs. A person in two groups is counted once in
  the total (the tool never adds up distinct counts).
* **Indicators with disaggregation.** Count, distinct count, sum, mean or **percent** (numerator / denominator,
  optionally of distinct people), each broken down by sex, age group, ward, month... with a total.
  Where possible they are live Excel formulas and every figure is calculated twice.
* **Percent tables.** `percent_of: row | column | total` adds a verified % table under a pivot.
* **Completeness report** per column, and a warning when a value in an indicator filter never occurs
  (typo protection, e.g. `Femal`).

## How it protects accuracy
* **Nothing disappears silently.** Rows read = analysed + quarantined + blank, or the run FAILS.
* **Bad rows are quarantined with a reason** (bad number, impossible date, value not allowed, missing required value,
  duplicate ID, wrong number of fields, unknown option, outside limits) – never "fixed" by guessing.
* **No type guessing.** Everything is read as text first; leading zeros survive; dates use only the formats you set;
  `nan`, `inf`, `1,2,3` are rejected as numbers.
* **Every pivot and indicator is calculated twice** by independent code (pandas vs plain Python with exact
  summation); any disagreement fails the run. Group figures must add up to totals; percentages must be 0-100%
  and tables must sum to 100%.
* **Excel recalculates for itself.** Pivot and indicator cells are live formulas over *Clean Data*, and the
  Checks sheet compares Excel's grouped totals with Excel's own totals.
* **Strict configuration.** Unknown/misspelt settings are errors. Unquoted `yes`/`no` (which YAML silently turns
  into true/false) are rejected with instructions to add quotes.
* **Formula-injection safe.** Text like `=HYPERLINK(...)` in your data stays text in Excel and CSV.
* **Too many rejects** (default > 10%) fails the run – usually the wrong file or wrong configuration.

## Reading the status
* **PASSED** – all checks passed, no warnings.
* **PASSED WITH WARNINGS** – numbers verified, but rows were rejected or values flagged. Read the warnings.
* **FAILED** – a check failed; do not use the numbers until the cause (Checks sheet / run.log) is understood.
* **FAILED (input could not be processed)** – no report; `ERROR.txt` says what to fix.

## Limits (be aware)
* Excel cannot create native PivotTables from outside Excel. These are formula-driven pivot tables plus charts;
  *Clean Data* is an Excel Table, so Insert → PivotTable works on it in two clicks.
* A pivot has one row field and at most one column field (make several pivots for more views).
* Distinct counts, min, max and median are fixed values in Excel (still verified by the second calculation).
* The tool reports counts, sums, means and percentages. It does **not** do significance tests, weighting or
  sampling adjustments – do those separately if your donor requires them.
* Percentages in indicators are exact for the data given; they are only as representative as the data itself.
* Numbers are 64-bit floats (≈15 significant digits): fine for money up to billions. Keep ID/phone numbers as `text`.
* "1,500" is read as 1500. If your data uses a decimal comma, set `decimal_separator: ","` and
  `thousands_separator: "."` on that column.
* Above `excel_formulas_max_rows` (20,000) cells are fixed values, still verified by the two-method check.
* Pseudonymised codes are not anonymous: whoever knows your `privacy_salt` could test guessed values.
  Keep it secret and share reports, not the configuration, outside your organisation.
* Run only one watcher per inbox.

## Tests
`python -m unittest discover -s tests`  (37 tests, including ground truth computed independently of the tool and
a scan proving no personal data appears in any output file).

## Web interface (upload in the browser)
```
pip install .
swa-tool serve -c config.yaml          # then open http://127.0.0.1:8080
```
Drop a CSV / Excel / JSON file on the page; the verified report appears with download buttons
(Excel, HTML, clean data, rejected rows, audit, or one .zip). **Settings** edits and validates `config.yaml`
in the browser; **Past runs** lists earlier results. The uploaded file is deleted as soon as the run ends;
only the reports are kept in `outputs/`.

Sharing with a team (e.g. an office server): `SWA_PASSWORD='long password' swa-tool serve -c config.yaml --host 0.0.0.0 --port 8080`.
Without `SWA_PASSWORD` the page is open to anyone who can reach it, so only do that on your own computer.
Put it behind HTTPS (e.g. a reverse proxy) before exposing it beyond your office network, because this is beneficiary data.
Optional: `SWA_MAX_MB=100` raises the upload limit (default 50).
