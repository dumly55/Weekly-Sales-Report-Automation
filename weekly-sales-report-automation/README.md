# Weekly Sales Report Automation

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

1. **Download** the raw export (cached after first run).
2. **Clean & validate**: drops exact duplicates, separates cancellations into a returns view instead of just deleting them, flags rows with missing customer IDs, and drops non-product adjustment rows — logging exactly how many rows were affected by each step.
3. **Compute KPIs**: revenue, units sold, orders, unique customers — for the target week *and* the prior week, with week-over-week % change. Also: top 10 products by revenue, revenue by country, and cancellation totals.
4. **Generate a formatted Excel workbook** with four sheets: `Summary` (KPI table + daily revenue trend chart), `Top Products` (table + chart), `By Country` (table + chart), and `Data Quality Log` (what got cleaned and why — an audit trail).
5. **Log the run** to `logs/run_<date>.log`.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python -m src.main --as-of-date 2011-11-28
```

Output: `output/weekly_report_2011-11-28.xlsx`

## Tests

```bash
pytest
```

## How this would run in production

In a real job, this would be scheduled to run every Monday morning against the latest export, with the output emailed or dropped into a shared drive. On Windows that's a one-line Task Scheduler entry:

```powershell
schtasks /create /tn "WeeklySalesReport" /tr "python -m src.main --as-of-date %date%" /sc weekly /d MON /st 06:00
```

(or an equivalent cron entry / Airflow DAG in a Linux/cloud environment). Not set up here since it's a portfolio demo, not a live job — but the pipeline is written to be trivially droppable into a scheduler as-is.


