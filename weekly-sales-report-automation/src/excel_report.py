"""Builds the formatted weekly sales Excel report from computed KPI data."""

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .clean import QualityLog
from .excel_style import (
    BAD_FONT,
    BOLD,
    GOOD_FONT,
    add_table_polish,
    write_dataframe,
    write_findings,
    write_header_row,
    write_title,
)
from .report import WeeklyReportData

COUNT_FORMAT = "#,##0"


def _currency_format(symbol: str) -> str:
    return f'"{symbol}"#,##0.00' if symbol else "#,##0.00"


def _revenue_label(symbol: str) -> str:
    return f"Revenue ({symbol})" if symbol else "Revenue"

# Distinct tab colors so the sheets are easy to tell apart at a glance.
TAB_COLORS = {
    "Summary": "1F4E78",
    "Top Products": "2E7D32",
    "By Country": "6A1B9A",
    "Data Quality Log": "757575",
}


def _autofit_first_column(ws: Worksheet, width: int = 22) -> None:
    ws.column_dimensions["A"].width = width


def _build_summary_sheet(ws: Worksheet, data: WeeklyReportData) -> None:
    write_title(ws, f"Weekly Sales Report: {data.week_label}", span_cols=4)
    _autofit_first_column(ws)
    currency_format = _currency_format(data.currency)
    cancellations_value_label = f"Cancellations (value, {data.currency})" if data.currency else "Cancellations (value)"

    # (label, this week, last week, wow % change, format kind)
    kpi_rows = [
        (_revenue_label(data.currency), data.current["revenue"], data.previous["revenue"], data.wow["revenue"], "currency"),
        ("Units Sold", data.current["units"], data.previous["units"], data.wow["units"], "count"),
        ("Orders", data.current["orders"], data.previous["orders"], data.wow["orders"], "count"),
        ("Unique Customers", data.current["customers"], data.previous["customers"], data.wow["customers"], "count"),
        ("Cancellations (count)", data.current["cancellations_count"], data.previous["cancellations_count"], None, "count"),
        (cancellations_value_label, data.current["cancellations_value"], data.previous["cancellations_value"], None, "currency"),
    ]
    kpi_df = pd.DataFrame([row[:4] for row in kpi_rows], columns=["Metric", "This Week", "Last Week", "WoW % Change"])

    header_row = 3
    last_row = write_dataframe(ws, kpi_df, header_row, zebra=True)

    # WoW values come from `kpi_rows` directly (not re-read from the cell) because pandas
    # coerces a mixed [float, None] column to float64 with NaN in place of None, and NaN
    # is an instance of float — reading it back would misidentify "n/a" rows as numeric.
    for offset, (_, _, _, wow_value, kind) in enumerate(kpi_rows):
        r = header_row + 1 + offset
        value_format = currency_format if kind == "currency" else COUNT_FORMAT
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

    last_row = write_findings(ws, data.findings, start_row=last_row + 2)

    # Daily revenue trend data (written off to the side, feeds the chart)
    chart_start_row = last_row + 3
    ws.cell(row=chart_start_row, column=1, value="Daily Revenue Trend").font = BOLD
    daily_last_row = write_dataframe(ws, data.daily, chart_start_row + 1, number_formats={1: currency_format})

    chart = LineChart()
    chart.title = "Daily Revenue Trend"
    chart.y_axis.title = _revenue_label(data.currency)
    chart.x_axis.title = "Date"
    values = Reference(ws, min_col=2, min_row=chart_start_row + 1, max_row=daily_last_row)
    categories = Reference(ws, min_col=1, min_row=chart_start_row + 2, max_row=daily_last_row)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(categories)
    chart.width = 20
    chart.height = 10
    ws.add_chart(chart, f"F{header_row}")


def _build_top_products_sheet(ws: Worksheet, data: WeeklyReportData) -> None:
    write_title(ws, "Top 10 Products by Revenue", span_cols=3)
    header_row = 3
    last_row = write_dataframe(
        ws, data.top_products, header_row, number_formats={2: _currency_format(data.currency), 3: COUNT_FORMAT}
    )
    add_table_polish(ws, header_row, last_row, last_col=len(data.top_products.columns))

    chart = BarChart()
    chart.title = "Top Products by Revenue"
    chart.y_axis.title = _revenue_label(data.currency)
    values = Reference(ws, min_col=3, min_row=header_row, max_row=last_row)
    categories = Reference(ws, min_col=2, min_row=header_row + 1, max_row=last_row)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(categories)
    chart.width = 22
    chart.height = 12
    ws.add_chart(chart, "F3")


def _build_by_country_sheet(ws: Worksheet, data: WeeklyReportData) -> None:
    write_title(ws, "Revenue by Country", span_cols=3)
    header_row = 3
    last_row = write_dataframe(
        ws, data.by_country, header_row, number_formats={1: _currency_format(data.currency), 2: COUNT_FORMAT}
    )
    add_table_polish(ws, header_row, last_row, last_col=len(data.by_country.columns))

    chart = BarChart()
    chart.title = "Revenue by Country"
    chart.y_axis.title = _revenue_label(data.currency)
    values = Reference(ws, min_col=2, min_row=header_row, max_row=last_row)
    categories = Reference(ws, min_col=1, min_row=header_row + 1, max_row=last_row)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(categories)
    chart.width = 22
    chart.height = 12
    ws.add_chart(chart, "E3")


def _build_quality_log_sheet(ws: Worksheet, quality_log: QualityLog) -> None:
    write_title(ws, "Data Quality Log", span_cols=3)
    _autofit_first_column(ws, 30)
    descriptions = {
        "rows_in": "Raw rows read from source export",
        "duplicates_dropped": "Exact duplicate rows removed",
        "unreadable_rows_dropped": "Rows dropped because the date, quantity or price couldn't be read",
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
    last_row = write_dataframe(ws, df, header_row, number_formats={1: COUNT_FORMAT})
    add_table_polish(ws, header_row, last_row, last_col=len(df.columns))


ORGANIZED_COLUMNS = {
    "InvoiceDate": ("Date", 18),
    "InvoiceNo": ("Order ID", 14),
    "StockCode": ("Product Code", 16),
    "Description": ("Product", 40),
    "Quantity": ("Quantity", 10),
    "UnitPrice": ("Unit Price", 12),
    "LineTotal": ("Line Total", 13),
    "CustomerID": ("Customer", 14),
    "Country": ("Country", 18),
}
# Above this many rows, only the two compared weeks are written, to keep the file a sensible size.
ORGANIZED_ROW_LIMIT = 100_000


def _build_organized_sheet(ws: Worksheet, sales_df: pd.DataFrame, data: WeeklyReportData) -> None:
    if len(sales_df) > ORGANIZED_ROW_LIMIT:
        in_compared_weeks = (sales_df["InvoiceDate"] >= data.week_start - pd.Timedelta(days=7)) & (
            sales_df["InvoiceDate"] < data.week_end
        )
        rows = sales_df[in_compared_weeks]
        title = f"Organized Data: the two weeks compared ({len(rows):,} of {len(sales_df):,} clean rows)"
    else:
        rows = sales_df
        title = f"Organized Data: all {len(rows):,} clean sales rows"

    rows = rows.sort_values(["InvoiceDate", "InvoiceNo"])[list(ORGANIZED_COLUMNS)]
    rows = rows.astype(object).where(rows.notna(), None)

    write_title(ws, title, span_cols=len(ORGANIZED_COLUMNS))
    header_row = 3
    write_header_row(ws, [label for label, _ in ORGANIZED_COLUMNS.values()], header_row)
    for values in rows.itertuples(index=False):
        ws.append(list(values))

    last_row = header_row + len(rows)
    formats = {1: "yyyy-mm-dd hh:mm", 6: _currency_format(data.currency), 7: _currency_format(data.currency), 8: "0"}
    for col, number_format in formats.items():
        for (cell,) in ws.iter_rows(min_row=header_row + 1, max_row=last_row, min_col=col, max_col=col):
            cell.number_format = number_format
    for col, (_, width) in enumerate(ORGANIZED_COLUMNS.values(), start=1):
        ws.column_dimensions[get_column_letter(col)].width = width

    add_table_polish(ws, header_row, max(last_row, header_row), last_col=len(ORGANIZED_COLUMNS))
    ws.sheet_properties.tabColor = "EF6C00"


def build_workbook(
    data: WeeklyReportData, quality_log: QualityLog, out_path: Path, organized_rows: pd.DataFrame | None = None
) -> None:
    """`organized_rows` (the cleaned sales rows), if given, adds an "Organized Data" sheet."""
    wb = Workbook()

    summary_ws = wb.active
    summary_ws.title = "Summary"
    _build_summary_sheet(summary_ws, data)

    _build_top_products_sheet(wb.create_sheet("Top Products"), data)
    _build_by_country_sheet(wb.create_sheet("By Country"), data)
    if organized_rows is not None:
        _build_organized_sheet(wb.create_sheet("Organized Data"), organized_rows, data)
    _build_quality_log_sheet(wb.create_sheet("Data Quality Log"), quality_log)

    for sheet_name, color in TAB_COLORS.items():
        wb[sheet_name].sheet_properties.tabColor = color

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
