"""Cleaning and validation for the raw Online Retail transaction export."""

import logging
from pathlib import Path
from typing import NamedTuple

import pandas as pd

logger = logging.getLogger(__name__)

RAW_COLUMNS = [
    "InvoiceNo",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "UnitPrice",
    "CustomerID",
    "Country",
]


class QualityLog(NamedTuple):
    rows_in: int
    duplicates_dropped: int
    cancellations_separated: int
    missing_customer_id: int
    non_positive_price_dropped: int
    rows_out: int


def validate_raw_columns(df: pd.DataFrame) -> None:
    """Raises a friendly ValueError if any expected column is missing.

    Shared by both the built-in demo dataset loader and the custom data-link
    loader, so a malformed file gives the same clear error either way.
    """
    missing = set(RAW_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Data is missing expected columns: {sorted(missing)}")


def load_raw(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0)
    validate_raw_columns(df)
    return df


def clean_transactions(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, QualityLog]:
    """Cleans raw transaction rows.

    Returns (sales_df, cancellations_df, quality_log). `sales_df` contains only
    valid, positive-value product sale lines with a computed LineTotal column.
    Cancellations are kept separately rather than silently dropped, since a
    real reporting job needs to account for returns.
    """
    rows_in = len(df)

    working = df.drop_duplicates()
    duplicates_dropped = rows_in - len(working)

    working["InvoiceDate"] = pd.to_datetime(working["InvoiceDate"])
    working["InvoiceNo"] = working["InvoiceNo"].astype(str)

    is_cancellation = working["InvoiceNo"].str.startswith("C")
    cancellations_df = working[is_cancellation].copy()
    cancellations_df["LineTotal"] = cancellations_df["Quantity"] * cancellations_df["UnitPrice"]
    cancellations_separated = len(cancellations_df)

    sales = working[~is_cancellation].copy()

    missing_customer_id = int(sales["CustomerID"].isna().sum())

    valid_price_mask = sales["UnitPrice"] > 0
    valid_qty_mask = sales["Quantity"] > 0
    non_positive_price_dropped = int((~(valid_price_mask & valid_qty_mask)).sum())
    sales = sales[valid_price_mask & valid_qty_mask].copy()

    sales["LineTotal"] = sales["Quantity"] * sales["UnitPrice"]

    quality_log = QualityLog(
        rows_in=rows_in,
        duplicates_dropped=duplicates_dropped,
        cancellations_separated=cancellations_separated,
        missing_customer_id=missing_customer_id,
        non_positive_price_dropped=non_positive_price_dropped,
        rows_out=len(sales),
    )

    logger.info("Cleaning summary: %s", quality_log._asdict())

    return sales, cancellations_df, quality_log
