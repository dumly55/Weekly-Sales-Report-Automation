# Weekly Sales Report Automation

[![Tests](https://github.com/dumly55/Weekly-Sales-Report-Automation/actions/workflows/tests.yml/badge.svg)](https://github.com/dumly55/Weekly-Sales-Report-Automation/actions/workflows/tests.yml)
[![Weekly Report](https://github.com/dumly55/Weekly-Sales-Report-Automation/actions/workflows/weekly-report.yml/badge.svg)](https://github.com/dumly55/Weekly-Sales-Report-Automation/actions/workflows/weekly-report.yml)

Turns a raw, messy e-commerce transaction export into a formatted, ready-to-send weekly Excel report — replacing what would otherwise be a manual, error-prone weekly task in Excel.

## The problem this simulates

A lot of small/mid-size retailers still generate their weekly sales report by hand: someone opens last week's raw transaction export, deletes duplicate rows, figures out which orders were cancelled, builds a couple of pivot tables, and emails a spreadsheet around. It's slow and easy to get wrong (e.g. forgetting to exclude cancellations, or double-counting duplicated rows).

This project automates that whole process into a single command.

## Dataset

Real transactional data: the [UCI "Online Retail" dataset](https://archive.ics.uci.edu/dataset/352/online+retail) — 541,909 line-item invoice records from a UK-based online retailer, Dec 2010–Dec 2011. It's messy in realistic ways:
- Cancelled orders (`InvoiceNo` starting with `C`, negative quantities)
- Missing `CustomerID` on some rows
- Non-product "adjustment" line items (postage, bank charges) with zero/odd pricing
- Duplicate rows

The script downloads and caches this automatically on first run — no manual download needed.

## What it does

Because the dataset is historical, the pipeline is driven by `--as-of-date` to simulate "this week's export just arrived." It filters to the Mon–Sun ISO week containing that date, as if it had been triggered by a Monday-morning scheduled job.

1. **Download** the raw export (cached after first run, with a progress bar on first download).
2. **Clean & validate**: drops exact duplicates, separates cancellations into a returns view instead of just deleting them, flags rows with missing customer IDs, and drops non-product adjustment rows — logging exactly how many rows were affected by each step.
3. **Compute KPIs**: revenue, units sold, orders, unique customers — for the target week *and* the prior week, with week-over-week % change. Also: top 10 products by revenue, revenue by country, and cancellation totals.
4. **Generate a formatted Excel workbook** with five sheets: `Summary` (KPI table + daily revenue trend chart), `Top Products` (table + chart), `By Country` (table + chart), `Organized Data` (your cleaned rows with clear column names, sorted by date; for data over 100,000 rows, just the two weeks compared), and `Data Quality Log` (what got cleaned and why — an audit trail). Each sheet has a frozen header row, autofilter, zebra-striped rows, color-coded tabs, and green/red week-over-week % changes.
5. **Print a colorized summary** to the console and **log the full run detail** to `logs/run_<date>.log`.

## Setup

```bash
pip install -r requirements.txt
```

## Don't want to use the terminal?

Double-click **`run_gui.bat`**. It opens a small window where you can optionally paste a data link and pick a week from a calendar (or keep the default, the latest week in the data), then click **Generate Report** — no commands to type. It reuses the exact same pipeline as the CLI below, just with a point-and-click front end (`src/gui.py`, launched via `run_gui.pyw`).

The data-source field accepts:
- A direct link to a CSV or Excel file
- A Google Sheet shared as **"Anyone with the link can view"** (rewritten automatically to its CSV export link)
- Left blank: uses the built-in demo dataset described below

## Using your own sales data

Your sheet doesn't need to match the demo dataset's layout. Columns are recognized by common names, ignoring case, spaces and punctuation:

| Field | Recognized names include | Required? | If missing |
|---|---|---|---|
| date | Date, Order Date, Invoice Date, Transaction Date, Timestamp | Yes | — |
| unit_price | Price, Unit Price, Price Each, Unit Cost | One of these two | Worked out as line_total ÷ quantity |
| line_total | Total, Amount, Sales, Revenue, Subtotal | | |
| quantity | Quantity, Qty, Units, Units Sold | No | Each row counts as 1 unit |
| order_id | Order ID, Invoice, Order Number, Transaction ID | No | Each row counts as its own order |
| product / product_code | Product, Item, Product Name / SKU, Product ID, Item Code | No | Fill in for each other, else "Unknown product" |
| customer | Customer ID, Customer, Client | No | Unique customers can't be counted |
| country | Country, Region, Market | No | "Unknown" |

Messy layouts are handled too:
- Title or note rows above the real header row are skipped (the first 10 rows are searched for the header).
- Blank rows are ignored.
- Amounts stored as text, like `$1,200.00` or `(15.00)`, are read as numbers, and dates can be in mixed formats.
- Rows whose date, quantity or price can't be read, such as a "Grand Total" row, are dropped and counted in the report's Data Quality Log sheet.

**When a column isn't recognized** (or the wrong one is picked), add a column map naming your sheet's columns, using the field names from the table above:

```bash
python -m src.main --data-url "<link>" --column-map "date=Placed On, line_total=Net Sales, customer=Buyer"
```

For the scheduled GitHub run, put the same text in a `COLUMN_MAP` repository variable (next to `DATA_URL`). Fields you leave out are still matched automatically.

**Currency:** your data is shown in US dollars (`$`), unless its amounts clearly use another symbol, like `€1,200.00`. To force a symbol, use `--currency "€"`, or a `CURRENCY` repository variable for the scheduled run. The demo dataset stays in £, since its amounts really are British pounds.

## Run (command line)

```bash
python -m src.main --as-of-date 2011-11-28
```

Leave off `--as-of-date` to report on the latest week that has sales in the data. Add `--markdown-summary <file>` to also append the KPI table to a Markdown file (used by the scheduled GitHub run below).

Add `--data-url "<link>"` to point it at your own CSV/Excel file or public Google Sheet instead of the demo dataset (same rules as the GUI field above).

Output: `output/weekly_report_2011-11-28.xlsx` (earlier reports are never overwritten: re-running the same week saves `weekly_report_2011-11-28 (2).xlsx`, and so on), plus a console summary:

```
        Weekly Sales Report - 2011-11-28 to 2011-12-04
┌──────────────────┬─────────────┬─────────────┬──────────────┐
│ Metric           │   This Week │   Last Week │ WoW % Change │
├──────────────────┼─────────────┼─────────────┼──────────────┤
│ Revenue          │ £323,398.70 │ £315,114.06 │        +2.6% │
│ Units Sold       │     156,489 │     156,628 │        -0.1% │
│ Orders           │         665 │         619 │        +7.4% │
│ Unique Customers │         516 │         492 │        +4.9% │
└──────────────────┴─────────────┴─────────────┴──────────────┘
Report written to output/weekly_report_2011-11-28.xlsx
```

If a date has no matching transactions, or if the download/input data fails, the CLI prints a plain-language message (with exit code 1 on failure) instead of a raw traceback — the full technical detail still goes to `logs/run_<date>.log`.

## Tests

```bash
pytest
```

76 tests covering the cleaning rules and messy-value parsing (`test_clean.py`), column recognition for differently shaped sheets (`test_normalize.py`), KPI/week-boundary math and summary formatting (`test_report.py`), the generated workbook's structure and a pandas `NaN`/`None` regression (`test_excel_report.py`), and the custom data-link handling including Google Sheet URL rewriting (`test_download_data.py`). The GUI (`src/gui.py`) isn't covered by automated tests since it needs a real display, but it reuses the same tested `run_pipeline` function as the CLI.

## Scheduled weekly run

The report generates itself every Monday at 06:00 UTC via GitHub Actions ([`weekly-report.yml`](../.github/workflows/weekly-report.yml)), with no one pressing a button. Each run:

1. Installs the project and fetches the data: the Google Sheet or file at the `DATA_URL` repository variable if one is set, otherwise the cached demo dataset.
2. Runs the full pipeline for the latest week in the data.
3. Shows the KPI table on the run's summary page.
4. Attaches the Excel report and run log as a downloadable artifact (kept for 90 days).

See past runs, or trigger one on demand with **Run workflow**, on the repo's **Actions** tab under **Weekly Report**.

**Pointing it at a live sheet:** in the repo, go to **Settings → Secrets and variables → Actions → Variables → New repository variable**, name it `DATA_URL`, and paste a Google Sheet link (shared as "Anyone with the link can view") or a direct CSV/Excel link. Each Monday's run then reads the sheet as it is at that moment, so edits made during the week show up in that week's report. Without `DATA_URL`, it uses the historical demo dataset, so every week's numbers are the same.

To run it on a local Windows machine instead, it's a one-line Task Scheduler entry:

```powershell
schtasks /create /tn "WeeklySalesReport" /tr "python -m src.main" /sc weekly /d MON /st 06:00
```
