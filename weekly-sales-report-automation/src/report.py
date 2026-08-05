"""KPI computation for the weekly sales report."""

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd


def week_bounds(as_of_date: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Returns (week_start, week_end) for the Mon-Sun ISO week containing as_of_date.

    week_end is exclusive (the following Monday), so filtering is `>= start, < end`.
    """
    monday = as_of_date - timedelta(days=as_of_date.weekday())
    start = pd.Timestamp(monday)
    end = start + pd.Timedelta(days=7)
    return start, end


def _filter_range(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["InvoiceDate"] >= start) & (df["InvoiceDate"] < end)]


def _summarize(sales_week: pd.DataFrame, cancellations_week: pd.DataFrame) -> dict:
    return {
        "revenue": float(sales_week["LineTotal"].sum()),
        "units": int(sales_week["Quantity"].sum()),
        "orders": int(sales_week["InvoiceNo"].nunique()),
        "customers": int(sales_week["CustomerID"].nunique()),
        "cancellations_count": int(cancellations_week["InvoiceNo"].nunique()),
        "cancellations_value": float(-cancellations_week["LineTotal"].sum()),
    }


def pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / previous * 100


def top_products(sales_week: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    grouped = (
        sales_week.groupby(["StockCode", "Description"], dropna=False)
        .agg(Revenue=("LineTotal", "sum"), UnitsSold=("Quantity", "sum"))
        .reset_index()
        .sort_values("Revenue", ascending=False)
        .head(n)
        .reset_index(drop=True)
    )
    return grouped


def revenue_by_country(sales_week: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        sales_week.groupby("Country")
        .agg(Revenue=("LineTotal", "sum"), Orders=("InvoiceNo", "nunique"))
        .reset_index()
        .sort_values("Revenue", ascending=False)
        .reset_index(drop=True)
    )
    return grouped


def daily_revenue(sales_week: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    daily = sales_week.groupby(sales_week["InvoiceDate"].dt.date)["LineTotal"].sum()
    full_range = pd.date_range(start, end - pd.Timedelta(days=1), freq="D").date
    daily = daily.reindex(full_range, fill_value=0.0)
    return pd.DataFrame({"Date": daily.index, "Revenue": daily.values})


@dataclass
class WeeklyReportData:
    week_start: pd.Timestamp
    week_end: pd.Timestamp
    current: dict
    previous: dict
    wow: dict
    top_products: pd.DataFrame
    by_country: pd.DataFrame
    daily: pd.DataFrame


def build_report_data(sales_df: pd.DataFrame, cancellations_df: pd.DataFrame, as_of_date: date) -> WeeklyReportData:
    start, end = week_bounds(as_of_date)
    prev_start, prev_end = start - pd.Timedelta(days=7), start

    sales_week = _filter_range(sales_df, start, end)
    cancels_week = _filter_range(cancellations_df, start, end)
    sales_prev = _filter_range(sales_df, prev_start, prev_end)
    cancels_prev = _filter_range(cancellations_df, prev_start, prev_end)

    current = _summarize(sales_week, cancels_week)
    previous = _summarize(sales_prev, cancels_prev)
    wow = {key: pct_change(current[key], previous[key]) for key in ("revenue", "units", "orders", "customers")}

    return WeeklyReportData(
        week_start=start,
        week_end=end,
        current=current,
        previous=previous,
        wow=wow,
        top_products=top_products(sales_week),
        by_country=revenue_by_country(sales_week),
        daily=daily_revenue(sales_week, start, end),
    )
