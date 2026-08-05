"""Builds the formatted weekly Excel report from computed KPI data."""

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .clean import QualityLog
from .report import WeeklyReportData

HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
TITLE_FONT = Font(size=14, bold=True)
BOLD = Font(bold=True)


def _write_title(ws: Worksheet, text: str, row: int = 1) -> None:
    ws.cell(row=row, column=1, value=text).font = TITLE_FONT


def _write_header_row(ws: Worksheet, headers: list[str], row: int, start_col: int = 1) -> None:
    for offset, header in enumerate(headers):
        cell = ws.cell(row=row, column=start_col + offset, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")


def _write_dataframe(ws: Worksheet, df: pd.DataFrame, start_row: int, start_col: int = 1) -> int:
    """Writes a DataFrame with a styled header row. Returns the last row written."""
    _write_header_row(ws, list(df.columns), start_row, start_col)
    for r, (_, row) in enumerate(df.iterrows(), start=start_row + 1):
        for c, value in enumerate(row):
            ws.cell(row=r, column=start_col + c, value=value)
    for offset, col in enumerate(df.columns):
        width = max(12, len(str(col)) + 2)
        ws.column_dimensions[get_column_letter(start_col + offset)].width = width
    return start_row + len(df)


def _autofit_first_column(ws: Worksheet, width: int = 22) -> None:
    ws.column_dimensions["A"].width = width


def _build_summary_sheet(ws: Worksheet, data: WeeklyReportData) -> None:
    _write_title(ws, f"Weekly Sales Report: {data.week_start.date()} to {(data.week_end - pd.Timedelta(days=1)).date()}")
    _autofit_first_column(ws)

    kpi_rows = [
        ("Revenue (GBP)", data.current["revenue"], data.previous["revenue"], data.wow["revenue"]),
        ("Units Sold", data.current["units"], data.previous["units"], data.wow["units"]),
        ("Orders", data.current["orders"], data.previous["orders"], data.wow["orders"]),
        ("Unique Customers", data.current["customers"], data.previous["customers"], data.wow["customers"]),
        ("Cancellations (count)", data.current["cancellations_count"], data.previous["cancellations_count"], None),
        ("Cancellations (value, GBP)", data.current["cancellations_value"], data.previous["cancellations_value"], None),
    ]
    kpi_df = pd.DataFrame(kpi_rows, columns=["Metric", "This Week", "Last Week", "WoW % Change"])

    header_row = 3
    last_row = _write_dataframe(ws, kpi_df, header_row)

    for r in range(header_row + 1, last_row + 1):
        wow_cell = ws.cell(row=r, column=4)
        if isinstance(wow_cell.value, (int, float)):
            wow_cell.number_format = "+0.0%;-0.0%"
            wow_cell.value = wow_cell.value / 100
        revenue_cell = ws.cell(row=r, column=2)
        if r in (header_row + 1, header_row + 6):
            revenue_cell.number_format = "£#,##0.00"
            ws.cell(row=r, column=3).number_format = "£#,##0.00"

    # Daily revenue trend data (written off to the side, feeds the chart)
    chart_start_row = last_row + 3
    ws.cell(row=chart_start_row, column=1, value="Daily Revenue Trend").font = BOLD
    daily_last_row = _write_dataframe(ws, data.daily, chart_start_row + 1)

    chart = LineChart()
    chart.title = "Daily Revenue Trend"
    chart.y_axis.title = "Revenue (GBP)"
    chart.x_axis.title = "Date"
    values = Reference(ws, min_col=2, min_row=chart_start_row + 1, max_row=daily_last_row)
    categories = Reference(ws, min_col=1, min_row=chart_start_row + 2, max_row=daily_last_row)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(categories)
    chart.width = 20
    chart.height = 10
    ws.add_chart(chart, f"F{header_row}")


def _build_top_products_sheet(ws: Worksheet, data: WeeklyReportData) -> None:
    _write_title(ws, "Top 10 Products by Revenue")
    last_row = _write_dataframe(ws, data.top_products, 3)
    for r in range(4, last_row + 2):
        ws.cell(row=r, column=3).number_format = "£#,##0.00"

    chart = BarChart()
    chart.title = "Top Products by Revenue"
    chart.y_axis.title = "Revenue (GBP)"
    values = Reference(ws, min_col=3, min_row=3, max_row=last_row)
    categories = Reference(ws, min_col=2, min_row=4, max_row=last_row)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(categories)
    chart.width = 22
    chart.height = 12
    ws.add_chart(chart, "F3")


def _build_by_country_sheet(ws: Worksheet, data: WeeklyReportData) -> None:
    _write_title(ws, "Revenue by Country")
    last_row = _write_dataframe(ws, data.by_country, 3)
    for r in range(4, last_row + 2):
        ws.cell(row=r, column=2).number_format = "£#,##0.00"

    chart = BarChart()
    chart.title = "Revenue by Country"
    chart.y_axis.title = "Revenue (GBP)"
    values = Reference(ws, min_col=2, min_row=3, max_row=last_row)
    categories = Reference(ws, min_col=1, min_row=4, max_row=last_row)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(categories)
    chart.width = 22
    chart.height = 12
    ws.add_chart(chart, "E3")


def _build_quality_log_sheet(ws: Worksheet, quality_log: QualityLog) -> None:
    _write_title(ws, "Data Quality Log")
    _autofit_first_column(ws, 30)
    descriptions = {
        "rows_in": "Raw rows read from source export",
        "duplicates_dropped": "Exact duplicate rows removed",
        "cancellations_separated": "Cancelled-order lines separated into returns reporting",
        "missing_customer_id": "Sales rows with a missing CustomerID (kept, revenue still counted)",
        "non_positive_price_dropped": "Non-product / adjustment rows dropped (zero or negative price/qty)",
        "rows_out": "Clean product-sale rows used for KPI calculations",
    }
    df = pd.DataFrame(
        [(key.replace("_", " ").title(), value, descriptions[key]) for key, value in quality_log._asdict().items()],
        columns=["Check", "Count", "Description"],
    )
    _write_dataframe(ws, df, 3)


def build_workbook(data: WeeklyReportData, quality_log: QualityLog, out_path: Path) -> None:
    wb = Workbook()

    summary_ws = wb.active
    summary_ws.title = "Summary"
    _build_summary_sheet(summary_ws, data)

    _build_top_products_sheet(wb.create_sheet("Top Products"), data)
    _build_by_country_sheet(wb.create_sheet("By Country"), data)
    _build_quality_log_sheet(wb.create_sheet("Data Quality Log"), quality_log)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
