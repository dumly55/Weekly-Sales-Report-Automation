"""CLI entrypoint: generates the automated weekly sales report.

Usage:
    python -m src.main --as-of-date 2011-11-28
"""

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
from rich.console import Console
from rich.table import Table

from .clean import QualityLog, clean_transactions, load_raw
from .download_data import StatusCallback, ensure_raw_data, fetch_remote_dataset
from .excel_report import build_workbook
from .report import WeeklyReportData, build_report_data

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
LOGS_DIR = PROJECT_ROOT / "logs"

console = Console()


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid date - use YYYY-MM-DD (e.g. 2011-11-28)") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the automated weekly sales report.")
    parser.add_argument(
        "--as-of-date",
        required=True,
        type=_parse_date,
        help="Date (YYYY-MM-DD) whose ISO week (Mon-Sun) should be reported on.",
    )
    parser.add_argument(
        "--data-url",
        default=None,
        help=(
            "Optional: a link to a CSV/Excel file, or a Google Sheet shared as "
            '"Anyone with the link can view", to use instead of the built-in demo dataset.'
        ),
    )
    return parser.parse_args()


def setup_logging(as_of_date: date) -> None:
    """Logs the full run detail to a file. Console output is handled separately via
    rich (progress bar, summary table, friendly errors), so no console handler here.

    Reconfigures the root logger's handlers on every call (rather than relying on
    `logging.basicConfig`'s "first call wins" behavior), so a long-lived process
    that runs the pipeline more than once -- the GUI -- gets a fresh log file
    per run instead of writing everything to whichever date was requested first.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"run_{as_of_date}.log"

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.INFO)


def describe_error(exc: Exception) -> str:
    """Translates an exception into a plain-language message, shared by the CLI
    and the GUI so a network failure, bad data, etc. reads the same either way.
    """
    if isinstance(exc, requests.exceptions.RequestException):
        return "Couldn't download the source data. Check your internet connection and try again."
    if isinstance(exc, ValueError):
        return f"Invalid input data: {exc}"
    if isinstance(exc, OSError):
        return f"Couldn't read or write a required file: {exc}"
    return "Something went wrong. Check the log file for details."


def run_pipeline(
    as_of_date: date,
    data_url: str | None = None,
    status_callback: StatusCallback | None = None,
) -> tuple[WeeklyReportData, QualityLog, Path]:
    """Runs the full pipeline (fetch -> clean -> compute KPIs -> build workbook)
    and returns the results. Shared by the CLI and the GUI -- neither one owns
    this logic, they just present it differently.

    `data_url`, if given, is a user-supplied CSV/Excel link or Google Sheet
    (see `download_data.fetch_remote_dataset`) instead of the built-in demo
    dataset. `status_callback`, if given, receives a short human-readable
    string at each stage instead of the CLI's console/log output.
    """
    notify = status_callback or (lambda _msg: None)
    logger = logging.getLogger("main")

    if data_url:
        raw_df = fetch_remote_dataset(data_url, status_callback=status_callback)
    else:
        notify("Checking for the cached demo dataset...")
        raw_path = ensure_raw_data(status_callback=status_callback)
        notify("Loading dataset...")
        raw_df = load_raw(raw_path)

    notify("Cleaning and validating data...")
    sales_df, cancellations_df, quality_log = clean_transactions(raw_df)

    notify("Computing KPIs...")
    report_data = build_report_data(sales_df, cancellations_df, as_of_date)

    notify("Building the Excel report...")
    out_path = OUTPUT_DIR / f"weekly_report_{as_of_date}.xlsx"
    build_workbook(report_data, quality_log, out_path)
    logger.info("Report written to %s", out_path)

    notify("Done.")
    return report_data, quality_log, out_path


def _print_summary(report_data: WeeklyReportData, out_path: Path) -> None:
    week_label = f"{report_data.week_start.date()} to {(report_data.week_end - pd.Timedelta(days=1)).date()}"

    table = Table(title=f"Weekly Sales Report - {week_label}", title_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("This Week", justify="right")
    table.add_column("Last Week", justify="right")
    table.add_column("WoW % Change", justify="right")

    def fmt_wow(value: float | None) -> str:
        if value is None:
            return "n/a"
        color = "green" if value >= 0 else "red"
        return f"[{color}]{value:+.1f}%[/{color}]"

    rows = [
        ("Revenue", f"£{report_data.current['revenue']:,.2f}", f"£{report_data.previous['revenue']:,.2f}", report_data.wow["revenue"]),
        ("Units Sold", f"{report_data.current['units']:,}", f"{report_data.previous['units']:,}", report_data.wow["units"]),
        ("Orders", f"{report_data.current['orders']:,}", f"{report_data.previous['orders']:,}", report_data.wow["orders"]),
        ("Unique Customers", f"{report_data.current['customers']:,}", f"{report_data.previous['customers']:,}", report_data.wow["customers"]),
    ]
    for metric, current, previous, wow in rows:
        table.add_row(metric, current, previous, fmt_wow(wow))

    console.print()
    console.print(table)
    console.print(f"[bold green]Report written[/bold green] to [bold]{out_path}[/bold]\n")


def main() -> None:
    args = parse_args()
    setup_logging(args.as_of_date)
    logger = logging.getLogger("main")
    logger.info("Starting weekly report run for as-of date %s", args.as_of_date)

    try:
        report_data, quality_log, out_path = run_pipeline(args.as_of_date, data_url=args.data_url)

        if report_data.current["orders"] == 0:
            console.print(
                f"[yellow]Warning:[/yellow] no transactions found for the week of {args.as_of_date}. "
                "The report will still be generated, but it will be empty."
            )

    except Exception as exc:
        logger.exception("Report generation failed")
        console.print(f"[bold red]Error:[/bold red] {describe_error(exc)}")
        sys.exit(1)

    _print_summary(report_data, out_path)


if __name__ == "__main__":
    main()
