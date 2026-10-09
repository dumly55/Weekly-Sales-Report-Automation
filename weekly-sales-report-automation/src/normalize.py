"""Turns a sales sheet in whatever shape it arrives into the columns and types the pipeline expects."""

import logging
import re
import warnings
from collections import Counter

import pandas as pd

logger = logging.getLogger(__name__)

CURRENCY_SYMBOLS = "£$€¥"
_NUMBER_JUNK = rf"[{CURRENCY_SYMBOLS},\s]"
_AMOUNT_WITH_SYMBOL = re.compile(rf"^-?\(?\s*([{CURRENCY_SYMBOLS}])\s*-?[\d,]*\.?\d+\s*\)?$")

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


def match_columns(columns: list, column_map: dict[str, str] | None = None, fields: dict = FIELDS) -> dict[str, str]:
    """Returns {field: sheet column}: the user's `column_map` first, then every other field in
    `fields` whose column could be recognized by name."""
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
    for field, (_, aliases) in fields.items():
        if field in mapping:
            continue
        for alias in aliases:
            column = by_simple_name.get(alias)
            if column is not None and column not in used:
                mapping[field] = column
                used.add(column)
                break
    return mapping


HEADER_SEARCH_ROWS = 10
# Each group needs at least one of its fields: a date, plus a unit price or a line total.
SALES_REQUIRED: list[tuple[str, ...]] = [("date",), ("unit_price", "line_total")]


def _dedupe(names: list[str]) -> list[str]:
    """Makes repeated header names unique the way pandas does: "Total", "Total.1", ..."""
    seen: Counter[str] = Counter()
    unique = []
    for name in names:
        unique.append(f"{name}.{seen[name]}" if seen[name] else name)
        seen[name] += 1
    return unique


def _match_if_usable(
    columns: list, column_map: dict[str, str] | None, fields: dict, required: list[tuple[str, ...]]
) -> dict[str, str] | None:
    try:
        mapping = match_columns(columns, column_map, fields)
    except ValueError:
        return None
    return mapping if all(any(f in mapping for f in group) for group in required) else None


def find_header(
    df: pd.DataFrame,
    fields: dict = FIELDS,
    required: list[tuple[str, ...]] = SALES_REQUIRED,
    column_map: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Returns (df with its real header row applied, {field: column}). If the first row isn't the
    header (e.g. a title block sits above it), the next few rows are searched for one that is."""
    mapping = _match_if_usable(list(df.columns), column_map, fields, required)
    if mapping is not None:
        return df, mapping

    for i in range(min(HEADER_SEARCH_ROWS, len(df))):
        header = _dedupe([str(v).strip() if pd.notna(v) else f"Unnamed {n}" for n, v in enumerate(df.iloc[i])])
        mapping = _match_if_usable(header, column_map, fields, required)
        if mapping is not None:
            logger.info("Skipped title/note rows above the real header row: %s", header)
            return df.iloc[i + 1 :].set_axis(header, axis=1).reset_index(drop=True), mapping

    # No usable header anywhere: match the original columns again so the caller reports what's missing.
    return df, match_columns(list(df.columns), column_map, fields)


def standardize(df: pd.DataFrame, column_map: dict[str, str] | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
    """Maps a sheet's own columns onto the pipeline's columns, filling in sensible defaults for
    anything optional that's missing. Returns (standardized_df, {field: sheet column used}).

    Only a date and either a unit price or a line total are required. `column_map`
    ({field: sheet column}) overrides automatic matching for the fields it names.
    If the first row isn't the header (e.g. a title row sits above it), the next few
    rows are searched for one that is.
    """
    df = df.dropna(how="all")
    df, mapping = find_header(df, FIELDS, SALES_REQUIRED, column_map)

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


def detect_currency(df: pd.DataFrame, sample_rows: int = 1000) -> str | None:
    """Returns the currency symbol used most in amount cells like "$1,200.00", or None if there are none.

    Only text cells can carry a symbol: Excel files store amounts as plain numbers.
    """
    counts: Counter[str] = Counter()
    for column in df.columns:
        values = df[column].head(sample_rows)
        if pd.api.types.is_numeric_dtype(values) or pd.api.types.is_datetime64_any_dtype(values):
            continue
        for value in values.dropna().astype(str):
            match = _AMOUNT_WITH_SYMBOL.match(value.strip())
            if match:
                counts[match.group(1)] += 1
    return counts.most_common(1)[0][0] if counts else None


def to_datetime(series: pd.Series) -> pd.Series:
    """Parses dates, falling back to value-by-value parsing for sheets that mix date formats.

    Values that still can't be read become NaT.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    dayfirst = _looks_day_first(series)
    with warnings.catch_warnings():
        # pandas warns when it can't infer a single format; mixed formats are expected here.
        warnings.simplefilter("ignore", UserWarning)
        parsed = pd.to_datetime(series, errors="coerce", dayfirst=dayfirst)
        unparsed = parsed.isna() & series.notna()
        if unparsed.any():
            parsed[unparsed] = pd.to_datetime(series[unparsed], errors="coerce", format="mixed", dayfirst=dayfirst)
    return parsed


_SLASH_DATE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}")


def _looks_day_first(series: pd.Series) -> bool:
    """True when some date like "15/01/2026" can only be day-first and none can only be month-first.

    Without such evidence (e.g. every value is like "03/04/2026"), dates are read month-first.
    """
    day_first = month_first = False
    for value in series.dropna().astype(str):
        match = _SLASH_DATE.match(value)
        if match:
            first, second = int(match.group(1)), int(match.group(2))
            day_first |= first > 12
            month_first |= second > 12
    return day_first and not month_first
