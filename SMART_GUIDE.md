# Smart analysis and "Ask your data"

Upload **any** Excel, CSV or JSON file - no configuration. The tool finds the table, cleans it, analyses it, and answers
questions in plain English. It is the default option on the home page; the older "Structured register" option (driven by
`config.yaml`) is still there for fixed forms such as Kobo registers.

## What it does automatically
1. **Finds the data.** Titles above the table, a table starting in column B, several tables on a sheet, lookup sheets,
   total rows, repeated header lines and "solution/answer" sheets that repeat the data are all recognised. A plain-language
   note says which table was used and what was ignored. Hidden sheets are skipped.
2. **Cleans it, and tells you exactly what changed.** Stray spaces are trimmed, `$15400` becomes the number 15400,
   spelling variants that differ only by capitals are merged, text that is clearly a date is read as a date (day/month order is
   never guessed - ambiguous dates stay as text), unreadable numbers are left blank and listed. Every step appears in the
   **Cleaning Log** sheet.
3. **Adds what is hidden in the data.** "BMW X5" gives a *Company* column ("BMW"); if the workbook has a lookup sheet
   (Company -> Country) it is joined automatically, and anything that does not match is named, not hidden.
4. **Analyses.** Column profile, how often each value appears, group comparisons, correlations, trends over years or
   months, unusual values, plus automatic pivot tables with charts (live Excel formulas, double-checked) and a plain-language
   list of key insights.
5. **Answers questions.** Questions found in the workbook (for example on a "Questions" sheet) and any you type are
   answered. You can keep asking on the results page.

## Questions it understands (examples)
* most / least popular `<column>` - ties are reported, not hidden
* average / median / total `<number>` per `<column>`   e.g. "average price per owner"
* which `<column>` has the highest average `<number>`
* how many ... (with conditions)   e.g. "how many automatic BMW cars are there?"
* what percentage of ... are ...
* what is the most expensive / cheapest / oldest / newest ...
* is `<number>` related to `<number>`  /  what affects `<number>`
* how has `<number>` changed by year
* top 3 `<column>`, how many different `<column>`, compare `<A>` and `<B>`
* conditions like "more than 100000 km", "after 2015", "between 2010 and 2015"
Typos in column names are tolerated. Every answer shows **how the question was read**, the **table behind it**, and
"calculated twice, both agree". If the two calculations ever disagree you get no answer instead of a wrong one. If the tool
cannot tell what you mean, it says so and suggests questions using your real column names - it never guesses.

## Optional AI help (off by default)
The built-in engine needs no internet and no key. If you want it to understand more unusual wording, set on the server:
```
SWA_AI_ENABLED=1
ANTHROPIC_API_KEY=<your key>
SWA_AI_MODEL=claude-haiku-5-5     (optional)
```
Only when the built-in rules cannot understand a question, an AI model translates it into a small calculation request. It
sees **column names and summary statistics only - never rows, and never columns detected as personal data**. The reply is
validated against your real columns; the AI never calculates anything, so every number is still computed and double-checked
by this tool. The privacy page and admin checklist change automatically when AI help is on. Using it costs API usage on your
account. (Built and unit-tested with a simulated AI reply; test it with your own key before relying on it.)

## Personal data
Columns that look like personal data (names, phone numbers, e-mail, address, national ID) are detected, hashed or masked in
every output, and excluded from questions and from anything sent to an AI.

## Limits
* Up to 100,000 rows per smart analysis (every figure is calculated twice).
* The tool reports patterns, counts, averages and correlations. It does not run significance tests or prove causes.
* "Popular" means "appears in the most rows". Ambiguous questions ("which country has the highest price?") use a stated
  assumption (the average) and tell you how to ask differently ("total", "maximum").
* One main table per file. Several tables with identical columns (for example one sheet per month) are stacked automatically.
* Old `.xls` files must be saved as `.xlsx` first.
