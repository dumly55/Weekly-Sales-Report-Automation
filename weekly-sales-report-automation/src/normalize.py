"""Turns a sales sheet in whatever shape it arrives into the columns and types the pipeline expects."""

import warnings

import pandas as pd

_NUMBER_JUNK = r"[£$€¥,\s]"


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
