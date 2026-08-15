from datetime import date

import pandas as pd
import pytest

from src.report import (
    build_report_data,
    daily_revenue,
    pct_change,
    revenue_by_country,
    top_products,
    week_bounds,
)


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
def sales_df():
    rows = [
        # current week (Mon 2011-11-21 - Sun 2011-11-27): revenue 180, units 18, 3 orders, 2 customers
        _sales_row("600001", "A1", "Widget", 10, "2011-11-21 10:00:00", 10.0, 1001, "United Kingdom"),
        _sales_row("600002", "B1", "Gadget", 5, "2011-11-23 11:00:00", 10.0, 1002, "France"),
        _sales_row("600003", "A1", "Widget", 3, "2011-11-25 09:00:00", 10.0, 1001, "United Kingdom"),
        # previous week (Mon 2011-11-14 - Sun 2011-11-20): revenue 100, units 10, 1 order, 1 customer
        _sales_row("500001", "A1", "Widget", 10, "2011-11-14 10:00:00", 10.0, 2001, "United Kingdom"),
        # outside both weeks - should never affect either week's numbers
        _sales_row("400001", "A1", "Widget", 99, "2011-10-01 10:00:00", 10.0, 3001, "United Kingdom"),
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def cancellations_df():
    rows = [
        # current week cancellation: quantity -2 * price 10 -> LineTotal -20, so cancellations_value = 20
        _sales_row("C600004", "A1", "Widget", -2, "2011-11-22 08:00:00", 10.0, 1001, "United Kingdom"),
    ]
    return pd.DataFrame(rows)


AS_OF_DATE = date(2011, 11, 24)  # a Thursday inside the current week fixture above


class TestWeekBounds:
    def test_returns_monday_to_next_monday(self):
        start, end = week_bounds(date(2011, 11, 24))
        assert start == pd.Timestamp("2011-11-21")
        assert end == pd.Timestamp("2011-11-28")

    def test_monday_as_of_date_is_its_own_week_start(self):
        start, end = week_bounds(date(2011, 11, 21))
        assert start == pd.Timestamp("2011-11-21")
        assert end == pd.Timestamp("2011-11-28")

    def test_sunday_as_of_date_stays_in_same_week(self):
        start, end = week_bounds(date(2011, 11, 27))
        assert start == pd.Timestamp("2011-11-21")
        assert end == pd.Timestamp("2011-11-28")

    def test_end_is_exclusive_next_monday(self):
        _, end = week_bounds(date(2011, 11, 24))
        assert end == pd.Timestamp("2011-11-28")


class TestPctChange:
    def test_increase(self):
        assert pct_change(150, 100) == pytest.approx(50.0)

    def test_decrease(self):
        assert pct_change(50, 100) == pytest.approx(-50.0)

    def test_zero_previous_returns_none(self):
        assert pct_change(10, 0) is None

    def test_zero_previous_and_current_returns_none(self):
        assert pct_change(0, 0) is None


class TestTopProducts:
    def test_ranks_by_revenue_descending(self, sales_df):
        current_week = sales_df[sales_df["InvoiceNo"].isin(["600001", "600002", "600003"])]
        result = top_products(current_week)
        assert list(result["StockCode"]) == ["A1", "B1"]
        assert result.loc[0, "Revenue"] == pytest.approx(130.0)
        assert result.loc[0, "UnitsSold"] == 13
        assert result.loc[1, "Revenue"] == pytest.approx(50.0)

    def test_respects_n_limit(self, sales_df):
        current_week = sales_df[sales_df["InvoiceNo"].isin(["600001", "600002", "600003"])]
        result = top_products(current_week, n=1)
        assert len(result) == 1
        assert result.loc[0, "StockCode"] == "A1"


class TestRevenueByCountry:
    def test_ranks_and_counts_orders_by_country(self, sales_df):
        current_week = sales_df[sales_df["InvoiceNo"].isin(["600001", "600002", "600003"])]
        result = revenue_by_country(current_week)
        assert list(result["Country"]) == ["United Kingdom", "France"]
        uk_row = result[result["Country"] == "United Kingdom"].iloc[0]
        assert uk_row["Revenue"] == pytest.approx(130.0)
        assert uk_row["Orders"] == 2
        france_row = result[result["Country"] == "France"].iloc[0]
        assert france_row["Revenue"] == pytest.approx(50.0)
        assert france_row["Orders"] == 1


class TestDailyRevenue:
    def test_fills_days_with_no_sales_as_zero(self, sales_df):
        start, end = week_bounds(AS_OF_DATE)
        current_week = sales_df[sales_df["InvoiceNo"].isin(["600001", "600002", "600003"])]
        result = daily_revenue(current_week, start, end)
        assert len(result) == 7
        by_date = dict(zip(result["Date"], result["Revenue"]))
        assert by_date[date(2011, 11, 21)] == pytest.approx(100.0)
        assert by_date[date(2011, 11, 23)] == pytest.approx(50.0)
        assert by_date[date(2011, 11, 25)] == pytest.approx(30.0)
        assert by_date[date(2011, 11, 22)] == pytest.approx(0.0)
        assert by_date[date(2011, 11, 26)] == pytest.approx(0.0)


class TestBuildReportData:
    def test_current_and_previous_week_kpis(self, sales_df, cancellations_df):
        data = build_report_data(sales_df, cancellations_df, AS_OF_DATE)

        assert data.week_start == pd.Timestamp("2011-11-21")
        assert data.week_end == pd.Timestamp("2011-11-28")

        assert data.current["revenue"] == pytest.approx(180.0)
        assert data.current["units"] == 18
        assert data.current["orders"] == 3
        assert data.current["customers"] == 2
        assert data.current["cancellations_count"] == 1
        assert data.current["cancellations_value"] == pytest.approx(20.0)

        assert data.previous["revenue"] == pytest.approx(100.0)
        assert data.previous["units"] == 10
        assert data.previous["orders"] == 1
        assert data.previous["customers"] == 1
        assert data.previous["cancellations_count"] == 0
        assert data.previous["cancellations_value"] == pytest.approx(0.0)

    def test_week_over_week_pct_change(self, sales_df, cancellations_df):
        data = build_report_data(sales_df, cancellations_df, AS_OF_DATE)
        assert data.wow["revenue"] == pytest.approx(80.0)
        assert data.wow["units"] == pytest.approx(80.0)
        assert data.wow["orders"] == pytest.approx(200.0)
        assert data.wow["customers"] == pytest.approx(100.0)

    def test_out_of_range_rows_are_excluded(self, sales_df, cancellations_df):
        # The fixture includes an October row that must not leak into either week.
        data = build_report_data(sales_df, cancellations_df, AS_OF_DATE)
        assert data.current["revenue"] + data.previous["revenue"] == pytest.approx(280.0)

    def test_no_previous_week_data_gives_none_wow(self, cancellations_df):
        sales_df = pd.DataFrame([_sales_row("600001", "A1", "Widget", 10, "2011-11-21 10:00:00", 10.0, 1001, "UK")])
        data = build_report_data(sales_df, cancellations_df.iloc[0:0], AS_OF_DATE)
        assert data.previous["revenue"] == 0.0
        assert data.wow["revenue"] is None
        assert data.wow["orders"] is None

    def test_daily_and_breakdown_frames_included(self, sales_df, cancellations_df):
        data = build_report_data(sales_df, cancellations_df, AS_OF_DATE)
        assert len(data.daily) == 7
        assert list(data.top_products["StockCode"]) == ["A1", "B1"]
        assert list(data.by_country["Country"]) == ["United Kingdom", "France"]
