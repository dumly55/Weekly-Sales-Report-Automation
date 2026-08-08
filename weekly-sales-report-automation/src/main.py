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

from .clean import clean_transactions, load_raw
from .download_data import ensure_raw_data
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
    return parser.parse_args()


def setup_logging(as_of_date: date) -> None:
    """Logs the full run detail to a file. Console output is handled separately via
    rich (progress bar, summary table, friendly errors), so no console handler here.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"run_{as_of_date}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file)],
    )


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
        raw_path = ensure_raw_data()
        raw_df = load_raw(raw_path)
        sales_df, cancellations_df, quality_log = clean_transactions(raw_df)
        report_data = build_report_data(sales_df, cancellations_df, args.as_of_date)

        if report_data.current["orders"] == 0:
            console.print(
                f"[yellow]Warning:[/yellow] no transactions found for the week of {args.as_of_date}. "
                "The report will still be generated, but it will be empty."
            )

        out_path = OUTPUT_DIR / f"weekly_report_{args.as_of_date}.xlsx"
        build_workbook(report_data, quality_log, out_path)
        logger.info("Report written to %s", out_path)

    except requests.exceptions.RequestException:
        logger.exception("Failed to download the source dataset")
        console.print("[bold red]Error:[/bold red] couldn't download the source dataset. Check your internet connection and try again.")
        sys.exit(1)
    except ValueError as exc:
        logger.exception("Invalid input data")
        console.print(f"[bold red]Error:[/bold red] invalid input data: {exc}")
        sys.exit(1)
    except OSError as exc:
        logger.exception("Filesystem error")
        console.print(f"[bold red]Error:[/bold red] couldn't read or write a required file: {exc}")
        sys.exit(1)
    except Exception:
        logger.exception("Unexpected error during report generation")
        console.print(
            f"[bold red]Error:[/bold red] something went wrong. See [bold]logs/run_{args.as_of_date}.log[/bold] for details."
        )
        sys.exit(1)

    _print_summary(report_data, out_path)


if __name__ == "__main__":
    main()
