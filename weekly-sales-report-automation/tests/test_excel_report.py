from datetime import date

import pandas as pd
import pytest
from openpyxl import load_workbook

from src.clean import QualityLog
from src.excel_report import TAB_COLORS, build_workbook
from src.report import build_report_data

EXPECTED_SHEETS = ["Summary", "Top Products", "By Country", "Data Quality Log"]


def _sales_row(invoice_no, stock_code, description, quantity, invoice_date, unit_price, customer_id, country):
    return {
        "InvoiceNo": invoice_no,
        "StockCode": stock_code,
        "Description": description,
        "Quantity": quantity,
        "InvoiceDate": pd.Timestamp(invoice_date),
        "UnitPrice": unit_price,
        "CustomerID": customer_id,
        "Country": country,
        "LineTotal": quantity * unit_price,
    }


@pytest.fixture
def quality_log():
    return QualityLog(
        rows_in=100,
        duplicates_dropped=2,
        cancellations_separated=3,
        missing_customer_id=1,
        non_positive_price_dropped=4,
        rows_out=90,
    )


@pytest.fixture
def report_data():
    """A normal week with both current and prior week sales, so WoW % is numeric."""
    sales_df = pd.DataFrame(
        [
            _sales_row("600001", "A1", "Widget", 10, "2011-11-21 10:00:00", 10.0, 1001, "United Kingdom"),
            _sales_row("600002", "B1", "Gadget", 5, "2011-11-23 11:00:00", 10.0, 1002, "France"),
            _sales_row("500001", "A1", "Widget", 10, "2011-11-14 10:00:00", 10.0, 2001, "United Kingdom"),
        ]
    )
    cancellations_df = pd.DataFrame(
        [_sales_row("C600004", "A1", "Widget", -2, "2011-11-22 08:00:00", 10.0, 1001, "United Kingdom")]
    )
    return build_report_data(sales_df, cancellations_df, date(2011, 11, 24))


@pytest.fixture
def report_data_no_previous_week():
    """A week with no prior-week sales at all, so every WoW % is None ('n/a').

    This is the scenario that previously triggered a bug: pandas coerces a
    mixed [float, None] KPI column to float64 with NaN standing in for None,
    and NaN passes an `isinstance(x, float)` check meant to detect real numbers.
    """
    sales_df = pd.DataFrame(
        [_sales_row("600001", "A1", "Widget", 10, "2011-11-21 10:00:00", 10.0, 1001, "United Kingdom")]
    )
    cancellations_df = pd.DataFrame(columns=list(sales_df.columns))
    return build_report_data(sales_df, cancellations_df, date(2011, 11, 24))


class TestBuildWorkbook:
    def test_creates_all_expected_sheets(self, report_data, quality_log, tmp_path):
        out_path = tmp_path / "report.xlsx"
        build_workbook(report_data, quality_log, out_path)

        assert out_path.exists()
        wb = load_workbook(out_path)
        assert wb.sheetnames == EXPECTED_SHEETS

    def test_sheet_tabs_are_colored(self, report_data, quality_log, tmp_path):
        out_path = tmp_path / "report.xlsx"
        build_workbook(report_data, quality_log, out_path)

        wb = load_workbook(out_path)
        for sheet_name, expected_color in TAB_COLORS.items():
            assert wb[sheet_name].sheet_properties.tabColor.rgb.endswith(expected_color)

    def test_data_sheets_have_frozen_header_and_autofilter(self, report_data, quality_log, tmp_path):
        out_path = tmp_path / "report.xlsx"
        build_workbook(report_data, quality_log, out_path)

        wb = load_workbook(out_path)
        for sheet_name in ("Top Products", "By Country", "Data Quality Log"):
            ws = wb[sheet_name]
            assert ws.freeze_panes == "A4"
            assert ws.auto_filter.ref is not None

    def test_creates_output_directory_if_missing(self, report_data, quality_log, tmp_path):
        out_path = tmp_path / "nested" / "dir" / "report.xlsx"
        build_workbook(report_data, quality_log, out_path)
        assert out_path.exists()

    def test_no_previous_week_data_does_not_crash_and_shows_na(
        self, report_data_no_previous_week, quality_log, tmp_path
    ):
        out_path = tmp_path / "report.xlsx"
        build_workbook(report_data_no_previous_week, quality_log, out_path)

        wb = load_workbook(out_path)
        ws = wb["Summary"]
        # Revenue row (row 4) WoW cell should read "n/a", not a stray numeric/NaN artifact.
        wow_cell = ws.cell(row=4, column=4)
        assert wow_cell.value == "n/a"

    def test_empty_top_products_and_by_country_do_not_crash(
        self, report_data_no_previous_week, quality_log, tmp_path
    ):
        # Sanity check that a minimal single-row week still produces valid breakdown sheets.
        out_path = tmp_path / "report.xlsx"
        build_workbook(report_data_no_previous_week, quality_log, out_path)
        wb = load_workbook(out_path)
        assert wb["Top Products"]["A3"].value == "StockCode"
        assert wb["By Country"]["A3"].value == "Country"
