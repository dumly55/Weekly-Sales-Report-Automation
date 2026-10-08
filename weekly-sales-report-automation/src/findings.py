"""Plain-English findings about the report week, from fixed rules (no AI), so the same data
always produces the same text. Each rule returns a sentence only when it has something to say."""

import pandas as pd

from .report import WeeklyReportData

FLAT_PCT = 0.5  # revenue changes smaller than this (in %) are described as flat
BENCHMARK_WEEKS = 4
COUNTRY_DRIVER_SHARE = 0.40  # name a country if it accounts for at least this share of the revenue change
PRODUCT_DRIVER_SHARE = 0.25
HIGH_CANCELLATION_SHARE = 0.10  # cancellations worth at least this share of revenue get called out


def _money(value: float, currency: str) -> str:
    return f"{'-' if value < 0 else ''}{currency}{abs(value):,.2f}"


def _in_range(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["InvoiceDate"] >= start) & (df["InvoiceDate"] < end)]


def _headline(data: WeeklyReportData) -> str:
    revenue, previous, pct = data.current["revenue"], data.previous["revenue"], data.wow["revenue"]
    if pct is None:
        return (
            f"Revenue was {_money(revenue, data.currency)} for {data.week_label}. "
            "There were no sales the week before to compare against."
        )
    if abs(pct) < FLAT_PCT:
        return f"Revenue was flat week over week at {_money(revenue, data.currency)} ({pct:+.1f}%)."
    verb = "rose" if pct > 0 else "fell"
    return (
        f"Revenue {verb} {abs(pct):.1f}% week over week, "
        f"from {_money(previous, data.currency)} to {_money(revenue, data.currency)}."
    )


def _partial_week(sales_df: pd.DataFrame, data: WeeklyReportData) -> str | None:
    last_sale = sales_df["InvoiceDate"].max()
    last_day_of_week = data.week_end - pd.Timedelta(days=1)
    if not (data.week_start <= last_sale and last_sale.normalize() < last_day_of_week):
        return None
    days = (last_sale.normalize() - data.week_start).days + 1
    return (
        f"Note: the data only runs through {last_sale:%A %Y-%m-%d} ({days} of 7 days this week), "
        "but it's compared with a full week before it, so the change isn't like-for-like."
    )


def _benchmark(sales_df: pd.DataFrame, data: WeeklyReportData) -> str | None:
    weekly = []
    for weeks_back in range(1, BENCHMARK_WEEKS + 1):
        start = data.week_start - pd.Timedelta(days=7 * weeks_back)
        week = _in_range(sales_df, start, start + pd.Timedelta(days=7))
        if not week.empty:
            weekly.append(week["LineTotal"].sum())
    if len(weekly) < 2:
        return None

    average = sum(weekly) / len(weekly)
    diff_pct = (data.current["revenue"] - average) / average * 100
    if abs(diff_pct) < FLAT_PCT:
        position = "in line with"
    else:
        position = f"{abs(diff_pct):.1f}% {'above' if diff_pct > 0 else 'below'}"
    return f"That's {position} the average of the {len(weekly)} weeks before it ({_money(average, data.currency)})."


def _orders_and_basket(data: WeeklyReportData) -> str | None:
    cur, prev, wow = data.current, data.previous, data.wow
    if not cur["orders"] or not prev["orders"]:
        return None

    aov, prev_aov = cur["revenue"] / cur["orders"], prev["revenue"] / prev["orders"]
    aov_pct = (aov - prev_aov) / prev_aov * 100
    sentence = (
        f"Orders went from {prev['orders']:,} to {cur['orders']:,} ({wow['orders']:+.1f}%), and the average "
        f"order value from {_money(prev_aov, data.currency)} to {_money(aov, data.currency)} ({aov_pct:+.1f}%)"
    )
    if abs(wow["revenue"]) < FLAT_PCT:
        return sentence + "."
    driver = "the number of orders" if abs(wow["orders"]) >= abs(aov_pct) else "the size of each order"
    return sentence + f", so the change came mostly from {driver}."


def _driver(
    this_week: pd.DataFrame, last_week: pd.DataFrame, column: str, threshold: float, currency: str
) -> str | None:
    """Names the single group (country or product) behind most of the revenue change, if there is one."""
    if last_week.empty:
        return None
    now = this_week.groupby(column)["LineTotal"].sum()
    before = last_week.groupby(column)["LineTotal"].sum()
    change = now.subtract(before, fill_value=0)
    total_change = change.sum()
    if len(change) < 2 or total_change == 0:
        return None

    name = (change if total_change > 0 else -change).idxmax()
    share = change[name] / total_change
    if share < threshold:
        return None

    was, is_now = before.get(name, 0.0), now.get(name, 0.0)
    movement = f"from {_money(was, currency)} to {_money(is_now, currency)}"
    if was:
        movement += f", {(is_now - was) / was * 100:+.1f}%"
    direction = "increase" if total_change > 0 else "decrease"
    if share > 1:
        return f"{name} alone more than accounted for the revenue {direction} ({movement})."
    return f"{name} drove {share:.0%} of the revenue {direction} ({movement})."


def _best_seller(this_week: pd.DataFrame, currency: str) -> str:
    by_product = this_week.groupby("Description")["LineTotal"].sum().sort_values(ascending=False)
    total = by_product.sum()
    top_name, top_revenue = by_product.index[0], by_product.iloc[0]
    sentence = f"Best seller: {top_name} ({_money(top_revenue, currency)}, {top_revenue / total:.0%} of revenue)."
    if len(by_product) > 10:
        sentence += f" The top 10 products made up {by_product.head(10).sum() / total:.0%} of revenue."
    return sentence


def _best_day(this_week: pd.DataFrame, currency: str) -> str | None:
    daily = this_week.groupby(this_week["InvoiceDate"].dt.date)["LineTotal"].sum()
    if len(daily) < 2:
        return None
    day = daily.idxmax()
    return (
        f"The strongest day was {day:%A %Y-%m-%d} "
        f"({_money(daily.max(), currency)}, {daily.max() / daily.sum():.0%} of the week's revenue)."
    )


def _cancellations(data: WeeklyReportData) -> str | None:
    count, value = data.current["cancellations_count"], data.current["cancellations_value"]
    if not count:
        return None
    revenue = data.current["revenue"]
    sentence = f"{count:,} order{'s were' if count != 1 else ' was'} cancelled, worth {_money(value, data.currency)}"
    if not revenue:
        return sentence + "."
    sentence += f" ({value / revenue:.1%} of revenue)."
    if value / revenue >= HIGH_CANCELLATION_SHARE:
        sentence += f" That's unusually high: revenue after cancellations was {_money(revenue - value, data.currency)}."
    return sentence


def build_findings(sales_df: pd.DataFrame, data: WeeklyReportData) -> list[str]:
    this_week = _in_range(sales_df, data.week_start, data.week_end)
    if this_week.empty:
        return [f"There were no sales in the week of {data.week_label}."]
    last_week = _in_range(sales_df, data.week_start - pd.Timedelta(days=7), data.week_start)

    candidates = [
        _headline(data),
        _partial_week(sales_df, data),
        _benchmark(sales_df, data),
        _orders_and_basket(data),
        _driver(this_week, last_week, "Country", COUNTRY_DRIVER_SHARE, data.currency),
        _driver(this_week, last_week, "Description", PRODUCT_DRIVER_SHARE, data.currency),
        _best_seller(this_week, data.currency),
        _best_day(this_week, data.currency),
        _cancellations(data),
    ]
    return [finding for finding in candidates if finding]
