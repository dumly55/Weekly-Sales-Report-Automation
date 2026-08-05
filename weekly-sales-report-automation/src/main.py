"""CLI entrypoint: generates the automated weekly sales report.

Usage:
    python -m src.main --as-of-date 2011-11-28
"""

import argparse
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from .clean import clean_transactions, load_raw
from .download_data import ensure_raw_data
from .excel_report import build_workbook
from .report import build_report_data

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
LOGS_DIR = PROJECT_ROOT / "logs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the automated weekly sales report.")
    parser.add_argument(
        "--as-of-date",
        required=True,
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        help="Date (YYYY-MM-DD) whose ISO week (Mon-Sun) should be reported on.",
    )
    return parser.parse_args()


def setup_logging(as_of_date) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"run_{as_of_date}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )


def main() -> None:
    args = parse_args()
    setup_logging(args.as_of_date)
    logger = logging.getLogger("main")

    logger.info("Starting weekly report run for as-of date %s", args.as_of_date)

    raw_path = ensure_raw_data()
    raw_df = load_raw(raw_path)
    sales_df, cancellations_df, quality_log = clean_transactions(raw_df)

    report_data = build_report_data(sales_df, cancellations_df, args.as_of_date)

    out_path = OUTPUT_DIR / f"weekly_report_{args.as_of_date}.xlsx"
    build_workbook(report_data, quality_log, out_path)

    logger.info("Report written to %s", out_path)
    logger.info(
        "Week %s to %s | Revenue: £%.2f (WoW %s) | Orders: %d | Customers: %d",
        report_data.week_start.date(),
        (report_data.week_end - pd.Timedelta(days=1)).date(),
        report_data.current["revenue"],
        f"{report_data.wow['revenue']:.1f}%" if report_data.wow["revenue"] is not None else "n/a",
        report_data.current["orders"],
        report_data.current["customers"],
    )


if __name__ == "__main__":
    main()
