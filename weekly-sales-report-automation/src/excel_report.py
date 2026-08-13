"""Builds the formatted weekly Excel report from computed KPI data."""

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .clean import QualityLog
from .report import WeeklyReportData

HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
TITLE_FONT = Font(size=16, bold=True, color="1F4E78")
BOLD = Font(bold=True)
ZEBRA_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
GOOD_FONT = Font(color="1F7A1F", bold=True)
BAD_FONT = Font(color="C00000", bold=True)

_GRID_SIDE = Side(style="thin", color="BFBFBF")
GRID_BORDER = Border(left=_GRID_SIDE, right=_GRID_SIDE, top=_GRID_SIDE, bottom=_GRID_SIDE)

CURRENCY_FORMAT = "£#,##0.00"
COUNT_FORMAT = "#,##0"

# Distinct tab colors so the sheets are easy to tell apart at a glance.
TAB_COLORS = {
    "Summary": "1F4E78",
    "Top Products": "2E7D32",
    "By Country": "6A1B9A",
    "Data Quality Log": "757575",
}


def _write_title(ws: Worksheet, text: str, span_cols: int, row: int = 1) -> None:
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = TITLE_FONT
    if span_cols > 1:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span_cols)


def _write_header_row(ws: Worksheet, headers: list[str], row: int, start_col: int = 1) -> None:
    for offset, header in enumerate(headers):
        cell = ws.cell(row=row, column=start_col + offset, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        cell.border = GRID_BORDER


def _write_dataframe(
    ws: Worksheet,
    df: pd.DataFrame,
    start_row: int,
    start_col: int = 1,
    number_formats: dict[int, str] | None = None,
    zebra: bool = True,
) -> int:
    """Writes a DataFrame with a styled, bordered header row and optional zebra striping.

    `number_formats` maps a 0-indexed column offset to a number format string,
    applied to every data cell in that column. Returns the last row written.
    """
    number_formats = number_formats or {}
    _write_header_row(ws, list(df.columns), start_row, start_col)

    for r, (_, row) in enumerate(df.iterrows(), start=start_row + 1):
        is_alt_row = zebra and (r - start_row) % 2 == 0
        for c, value in enumerate(row):
            cell = ws.cell(row=r, column=start_col + c, value=value)
            cell.border = GRID_BORDER
            if c in number_formats:
                cell.number_format = number_formats[c]
            if is_alt_row:
                cell.fill = ZEBRA_FILL

    last_row = start_row + len(df)
    for offset, col in enumerate(df.columns):
        content_width = max((len(str(v)) for v in df[col]), default=0)
        width = min(40, max(12, len(str(col)) + 2, content_width + 2))
        ws.column_dimensions[get_column_letter(start_col + offset)].width = width

    return last_row


def _add_table_polish(ws: Worksheet, header_row: int, last_row: int, last_col: int) -> None:
    """Adds an autofilter and freezes the header row so it stays visible while scrolling."""
    last_col_letter = get_column_letter(last_col)
    ws.auto_filter.ref = f"A{header_row}:{last_col_letter}{last_row}"
    ws.freeze_panes = f"A{header_row + 1}"


def _autofit_first_column(ws: Worksheet, width: int = 22) -> None:
    ws.column_dimensions["A"].width = width


def _build_summary_sheet(ws: Worksheet, data: WeeklyReportData) -> None:
    week_label = f"{data.week_start.date()} to {(data.week_end - pd.Timedelta(days=1)).date()}"
    _write_title(ws, f"Weekly Sales Report: {week_label}", span_cols=4)
    _autofit_first_column(ws)

    # (label, this week, last week, wow % change, format kind)
    kpi_rows = [
        ("Revenue (GBP)", data.current["revenue"], data.previous["revenue"], data.wow["revenue"], "currency"),
        ("Units Sold", data.current["units"], data.previous["units"], data.wow["units"], "count"),
        ("Orders", data.current["orders"], data.previous["orders"], data.wow["orders"], "count"),
        ("Unique Customers", data.current["customers"], data.previous["customers"], data.wow["customers"], "count"),
        ("Cancellations (count)", data.current["cancellations_count"], data.previous["cancellations_count"], None, "count"),
        ("Cancellations (value, GBP)", data.current["cancellations_value"], data.previous["cancellations_value"], None, "currency"),
    ]
    kpi_df = pd.DataFrame([row[:4] for row in kpi_rows], columns=["Metric", "This Week", "Last Week", "WoW % Change"])

    header_row = 3
    last_row = _write_dataframe(ws, kpi_df, header_row, zebra=True)

    # WoW values come from `kpi_rows` directly (not re-read from the cell) because pandas
    # coerces a mixed [float, None] column to float64 with NaN in place of None, and NaN
    # is an instance of float — reading it back would misidentify "n/a" rows as numeric.
    for offset, (_, _, _, wow_value, kind) in enumerate(kpi_rows):
        r = header_row + 1 + offset
        value_format = CURRENCY_FORMAT if kind == "currency" else COUNT_FORMAT
        ws.cell(row=r, column=2).number_format = value_format
        ws.cell(row=r, column=3).number_format = value_format

        wow_cell = ws.cell(row=r, column=4)
        if wow_value is None:
            wow_cell.value = "n/a"
            wow_cell.alignment = Alignment(horizontal="center")
        else:
            wow_cell.value = wow_value / 100
            wow_cell.number_format = "+0.0%;-0.0%"
            wow_cell.font = GOOD_FONT if wow_value >= 0 else BAD_FONT

    ws.freeze_panes = f"B{header_row + 1}"

    # Daily revenue trend data (written off to the side, feeds the chart)
    chart_start_row = last_row + 3
    ws.cell(row=chart_start_row, column=1, value="Daily Revenue Trend").font = BOLD
    daily_last_row = _write_dataframe(ws, data.daily, chart_start_row + 1, number_formats={1: CURRENCY_FORMAT})

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
    _write_title(ws, "Top 10 Products by Revenue", span_cols=3)
    header_row = 3
    last_row = _write_dataframe(
        ws, data.top_products, header_row, number_formats={2: CURRENCY_FORMAT, 3: COUNT_FORMAT}
    )
    _add_table_polish(ws, header_row, last_row, last_col=len(data.top_products.columns))

    chart = BarChart()
    chart.title = "Top Products by Revenue"
    chart.y_axis.title = "Revenue (GBP)"
    values = Reference(ws, min_col=3, min_row=header_row, max_row=last_row)
    categories = Reference(ws, min_col=2, min_row=header_row + 1, max_row=last_row)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(categories)
    chart.width = 22
    chart.height = 12
    ws.add_chart(chart, "F3")


def _build_by_country_sheet(ws: Worksheet, data: WeeklyReportData) -> None:
    _write_title(ws, "Revenue by Country", span_cols=3)
    header_row = 3
    last_row = _write_dataframe(
        ws, data.by_country, header_row, number_formats={1: CURRENCY_FORMAT, 2: COUNT_FORMAT}
    )
    _add_table_polish(ws, header_row, last_row, last_col=len(data.by_country.columns))

    chart = BarChart()
    chart.title = "Revenue by Country"
    chart.y_axis.title = "Revenue (GBP)"
    values = Reference(ws, min_col=2, min_row=header_row, max_row=last_row)
    categories = Reference(ws, min_col=1, min_row=header_row + 1, max_row=last_row)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(categories)
    chart.width = 22
    chart.height = 12
    ws.add_chart(chart, "E3")


def _build_quality_log_sheet(ws: Worksheet, quality_log: QualityLog) -> None:
    _write_title(ws, "Data Quality Log", span_cols=3)
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
    header_row = 3
    last_row = _write_dataframe(ws, df, header_row, number_formats={1: COUNT_FORMAT})
    _add_table_polish(ws, header_row, last_row, last_col=len(df.columns))


def build_workbook(data: WeeklyReportData, quality_log: QualityLog, out_path: Path) -> None:
    wb = Workbook()

    summary_ws = wb.active
    summary_ws.title = "Summary"
    _build_summary_sheet(summary_ws, data)

    _build_top_products_sheet(wb.create_sheet("Top Products"), data)
    _build_by_country_sheet(wb.create_sheet("By Country"), data)
    _build_quality_log_sheet(wb.create_sheet("Data Quality Log"), quality_log)

    for sheet_name, color in TAB_COLORS.items():
        wb[sheet_name].sheet_properties.tabColor = color

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
