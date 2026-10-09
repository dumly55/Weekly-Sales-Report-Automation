import pandas as pd
import pytest

from src.clean import clean_transactions
from src.normalize import to_datetime, to_number


@pytest.fixture
def raw_df():
    rows = [
        # normal valid sale
        ("536365", "85123A", "WHITE HANGING HEART", 6, "2011-11-22 08:26:00", 2.55, 17850.0, "United Kingdom"),
        # exact duplicate of the row above -> should be dropped
        ("536365", "85123A", "WHITE HANGING HEART", 6, "2011-11-22 08:26:00", 2.55, 17850.0, "United Kingdom"),
        # cancellation -> separated out, not counted as a sale
        ("C536366", "85123A", "WHITE HANGING HEART", -2, "2011-11-22 09:00:00", 2.55, 17850.0, "United Kingdom"),
        # missing CustomerID -> kept in sales, flagged
        ("536367", "22423", "REGENCY CAKESTAND", 4, "2011-11-23 10:00:00", 12.75, None, "France"),
        # postage line (not a product), even at a zero price -> dropped as non-product
        ("536368", "POST", "POSTAGE", 1, "2011-11-23 11:00:00", 0.0, 17850.0, "United Kingdom"),
        # negative quantity, not a cancellation invoice -> dropped
        ("536369", "22423", "REGENCY CAKESTAND", -1, "2011-11-23 12:00:00", 12.75, 17850.0, "United Kingdom"),
    ]
    columns = [
        "InvoiceNo",
        "StockCode",
        "Description",
        "Quantity",
        "InvoiceDate",
        "UnitPrice",
        "CustomerID",
        "Country",
    ]
    return pd.DataFrame(rows, columns=columns)


def test_duplicates_are_dropped(raw_df):
    sales, _, quality_log = clean_transactions(raw_df)
    assert quality_log.duplicates_dropped == 1


def test_cancellations_are_separated_not_in_sales(raw_df):
    sales, cancellations, quality_log = clean_transactions(raw_df)
    assert quality_log.cancellations_separated == 1
    assert not sales["InvoiceNo"].str.startswith("C").any()
    assert cancellations.iloc[0]["InvoiceNo"] == "C536366"


def test_order_ids_starting_with_c_but_not_c_digit_are_sales(raw_df):
    raw_df.loc[0, "InvoiceNo"] = "CA-2016-152156"
    sales, cancellations, _ = clean_transactions(raw_df)
    assert "CA-2016-152156" in sales["InvoiceNo"].values
    assert "CA-2016-152156" not in cancellations["InvoiceNo"].values


def test_missing_customer_id_is_flagged_but_kept(raw_df):
    sales, _, quality_log = clean_transactions(raw_df)
    assert quality_log.missing_customer_id == 1
    assert sales["CustomerID"].isna().sum() == 1


def test_non_positive_price_or_qty_rows_dropped(raw_df):
    sales, _, quality_log = clean_transactions(raw_df)
    assert quality_log.non_positive_price_dropped == 1
    assert (sales["Quantity"] > 0).all()
    assert (sales["UnitPrice"] > 0).all()


def test_non_product_lines_are_dropped_and_counted(raw_df):
    paid_postage = pd.DataFrame(
        [("536370", "DOT", " DOTCOM POSTAGE ", 1, "2011-11-24 10:00:00", 950.0, 17850.0, "United Kingdom")],
        columns=raw_df.columns,
    )
    sales, _, quality_log = clean_transactions(pd.concat([raw_df, paid_postage], ignore_index=True))
    assert quality_log.non_product_dropped == 2
    assert not sales["StockCode"].isin(["POST", "DOT"]).any()


def test_line_total_is_computed(raw_df):
    sales, _, _ = clean_transactions(raw_df)
    row = sales[sales["StockCode"] == "85123A"].iloc[0]
    assert row["LineTotal"] == pytest.approx(6 * 2.55)


def test_clean_data_has_no_unreadable_rows(raw_df):
    _, _, quality_log = clean_transactions(raw_df)
    assert quality_log.unreadable_rows_dropped == 0


def test_messy_text_values_are_parsed():
    messy = pd.DataFrame(
        {
            "InvoiceNo": ["1", "2", "3", "4"],
            "StockCode": ["A", "B", "C", "D"],
            "Description": ["a", "b", "c", "d"],
            "Quantity": ["2", "1,000", " 3 ", "1"],
            "InvoiceDate": ["2024-01-15 10:00", "01/16/2024", "Jan 17, 2024", "2024-01-18"],
            "UnitPrice": ["$1,200.00", "£0.50", "4", "€2.25"],
            "CustomerID": [1, 2, 3, 4],
            "Country": ["US", "US", "US", "US"],
        }
    )
    sales, _, quality_log = clean_transactions(messy)

    assert quality_log.unreadable_rows_dropped == 0
    assert list(sales["Quantity"]) == [2, 1000, 3, 1]
    assert list(sales["UnitPrice"]) == pytest.approx([1200.0, 0.5, 4.0, 2.25])
    assert [d.date().isoformat() for d in sales["InvoiceDate"]] == ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18"]


def test_unreadable_rows_are_dropped_and_counted():
    messy = pd.DataFrame(
        {
            "InvoiceNo": ["1", "2", "3"],
            "StockCode": ["A", "B", "C"],
            "Description": ["a", "b", "c"],
            "Quantity": ["2", "lots", "1"],
            "InvoiceDate": ["2024-01-15", "2024-01-16", "not a date"],
            "UnitPrice": ["5.00", "5.00", "5.00"],
            "CustomerID": [1, 2, 3],
            "Country": ["US", "US", "US"],
        }
    )
    sales, _, quality_log = clean_transactions(messy)

    assert quality_log.unreadable_rows_dropped == 2
    assert list(sales["InvoiceNo"]) == ["1"]


def test_day_first_dates_are_detected():
    parsed = to_datetime(pd.Series(["15/01/2026", "05/02/2026"]))
    assert [d.date().isoformat() for d in parsed] == ["2026-01-15", "2026-02-05"]


def test_ambiguous_dates_stay_month_first():
    parsed = to_datetime(pd.Series(["01/15/2026", "05/02/2026"]))
    assert [d.date().isoformat() for d in parsed] == ["2026-01-15", "2026-05-02"]


def test_accounting_negative_is_parsed_as_negative():
    assert to_number(pd.Series(["(15.00)"])).iloc[0] == pytest.approx(-15.0)


def test_row_counts_are_consistent(raw_df):
    sales, cancellations, quality_log = clean_transactions(raw_df)
    assert quality_log.rows_in == len(raw_df)
    assert quality_log.rows_out == len(sales)
    assert len(sales) == 2
