"""Turns a sales sheet in whatever shape it arrives into the columns and types the pipeline expects."""

import logging
import re
import warnings

import pandas as pd

logger = logging.getLogger(__name__)

_NUMBER_JUNK = r"[£$€¥,\s]"

# Field name (as users write it in a column map) -> (internal column, recognized header names).
# Header names are compared lowercased with everything but letters and digits removed,
# so "Order Date", "order_date" and "ORDER-DATE" all match "orderdate". Earlier names win.
FIELDS: dict[str, tuple[str, list[str]]] = {
    "date": ("InvoiceDate", ["invoicedate", "date", "orderdate", "transactiondate", "saledate", "salesdate", "purchasedate", "datetime", "timestamp", "createdat"]),
    "order_id": ("InvoiceNo", ["invoiceno", "invoice", "invoicenumber", "invoiceid", "orderid", "order", "orderno", "ordernumber", "transactionid", "transactionno", "receiptno", "receiptnumber"]),
    "product_code": ("StockCode", ["stockcode", "sku", "productid", "productcode", "itemid", "itemcode", "itemno", "itemnumber", "productno", "productnumber"]),
    "product": ("Description", ["description", "product", "productname", "item", "itemname", "itemdescription", "productdescription"]),
    "quantity": ("Quantity", ["quantity", "qty", "units", "unitssold", "quantitysold", "quantityordered"]),
    "unit_price": ("UnitPrice", ["unitprice", "price", "priceeach", "unitcost", "priceperunit", "saleprice", "sellingprice"]),
    "line_total": ("LineTotal", ["total", "linetotal", "amount", "sales", "revenue", "totalprice", "totalamount", "totalsales", "saleamount", "salesamount", "subtotal"]),
    "customer": ("CustomerID", ["customerid", "customer", "customerno", "customernumber", "customername", "clientid", "client", "clientname", "buyer"]),
    "country": ("Country", ["country", "region", "market", "location", "territory"]),
}


def _simplify(header: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


def parse_column_map(text: str) -> dict[str, str]:
    """Parses "date=Order Placed, line_total=Net Sales" (commas, semicolons or newlines between pairs)."""
    column_map: dict[str, str] = {}
    for pair in re.split(r"[,;\n]", text):
        if not pair.strip():
            continue
        field, sep, column = pair.partition("=")
        field, column = field.strip().lower(), column.strip()
        if not sep or not column:
            raise ValueError(f'Column map entry "{pair.strip()}" should look like field=Column Name.')
        if field not in FIELDS:
            raise ValueError(f'Unknown field "{field}" in column map. Fields are: {", ".join(FIELDS)}.')
        column_map[field] = column
    return column_map


def match_columns(columns: list, column_map: dict[str, str] | None = None) -> dict[str, str]:
    """Returns {field: sheet column}: the user's `column_map` first, then every other field whose
    column could be recognized by name."""
    mapping: dict[str, str] = {}
    for field, column in (column_map or {}).items():
        matches = [c for c in columns if str(c).strip().lower() == column.lower()]
        if not matches:
            raise ValueError(
                f'Column map says {field}="{column}", but the sheet has no such column. '
                f"The sheet's columns are: {', '.join(str(c) for c in columns)}."
            )
        mapping[field] = matches[0]

    by_simple_name: dict[str, str] = {}
    for column in columns:
        by_simple_name.setdefault(_simplify(column), column)

    used = set(mapping.values())
    for field, (_, aliases) in FIELDS.items():
        if field in mapping:
            continue
        for alias in aliases:
            column = by_simple_name.get(alias)
            if column is not None and column not in used:
                mapping[field] = column
                used.add(column)
                break
    return mapping


def standardize(df: pd.DataFrame, column_map: dict[str, str] | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
    """Maps a sheet's own columns onto the pipeline's columns, filling in sensible defaults for
    anything optional that's missing. Returns (standardized_df, {field: sheet column used}).

    Only a date and either a unit price or a line total are required. `column_map`
    ({field: sheet column}) overrides automatic matching for the fields it names.
    """
    mapping = match_columns(list(df.columns), column_map)

    missing = []
    if "date" not in mapping:
        missing.append("date")
    if "unit_price" not in mapping and "line_total" not in mapping:
        missing.append("unit_price (or line_total)")
    if missing:
        raise ValueError(
            f"Couldn't find a column for: {', '.join(missing)}. "
            f"The sheet's columns are: {', '.join(str(c) for c in df.columns)}. "
            'Add a column map to point at the right ones, e.g. "date=Order Placed, line_total=Net Sales".'
        )

    def column(field: str) -> pd.Series:
        return df[mapping[field]]

    out = pd.DataFrame(index=df.index)
    out["InvoiceDate"] = column("date")
    out["Quantity"] = column("quantity") if "quantity" in mapping else 1

    if "unit_price" in mapping:
        out["UnitPrice"] = column("unit_price")
    else:
        out["UnitPrice"] = to_number(column("line_total")) / to_number(out["Quantity"])

    # Without an order ID, each row counts as its own order (labelled by its sheet row number).
    out["InvoiceNo"] = column("order_id") if "order_id" in mapping else [str(i + 2) for i in range(len(df))]

    code = column("product_code") if "product_code" in mapping else None
    name = column("product") if "product" in mapping else None
    out["StockCode"] = code if code is not None else (name if name is not None else "Unknown product")
    out["Description"] = name if name is not None else (code if code is not None else "Unknown product")

    out["CustomerID"] = column("customer") if "customer" in mapping else float("nan")
    out["Country"] = column("country") if "country" in mapping else "Unknown"

    logger.info("Column mapping (field -> sheet column): %s", mapping)
    return out, mapping


def to_number(series: pd.Series) -> pd.Series:
    """Parses numbers stored as text, like "$1,200.00" or "(15.00)" (an accounting-style negative).

    Values that still can't be read become NaN.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series
    text = series.astype(str).str.strip().str.replace(_NUMBER_JUNK, "", regex=True)
    text = text.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    return pd.to_numeric(text, errors="coerce").astype("float64")


def to_datetime(series: pd.Series) -> pd.Series:
    """Parses dates, falling back to value-by-value parsing for sheets that mix date formats.

    Values that still can't be read become NaT.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    with warnings.catch_warnings():
        # pandas warns when it can't infer a single format; mixed formats are expected here.
        warnings.simplefilter("ignore", UserWarning)
        parsed = pd.to_datetime(series, errors="coerce")
        unparsed = parsed.isna() & series.notna()
        if unparsed.any():
            parsed[unparsed] = pd.to_datetime(series[unparsed], errors="coerce", format="mixed")
    return parsed
