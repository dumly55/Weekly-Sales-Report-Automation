import pandas as pd
import pytest

from src.clean import clean_transactions


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
        # non-positive price adjustment row -> dropped
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


def test_missing_customer_id_is_flagged_but_kept(raw_df):
    sales, _, quality_log = clean_transactions(raw_df)
    assert quality_log.missing_customer_id == 1
    assert sales["CustomerID"].isna().sum() == 1


def test_non_positive_price_or_qty_rows_dropped(raw_df):
    sales, _, quality_log = clean_transactions(raw_df)
    assert quality_log.non_positive_price_dropped == 2
    assert "POST" not in sales["StockCode"].values
    assert (sales["Quantity"] > 0).all()
    assert (sales["UnitPrice"] > 0).all()


def test_line_total_is_computed(raw_df):
    sales, _, _ = clean_transactions(raw_df)
    row = sales[sales["StockCode"] == "85123A"].iloc[0]
    assert row["LineTotal"] == pytest.approx(6 * 2.55)


def test_row_counts_are_consistent(raw_df):
    sales, cancellations, quality_log = clean_transactions(raw_df)
    assert quality_log.rows_in == len(raw_df)
    assert quality_log.rows_out == len(sales)
    assert len(sales) == 2
