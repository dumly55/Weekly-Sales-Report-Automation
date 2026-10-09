"""What to tell the reader about a prediction comparison: plain-English findings and the scorecard
(how accurate the predictions were, which movies are behind each measure, and what isn't scored yet).

Fixed rules, no AI, so the same sheets always give the same text.
"""

from dataclasses import dataclass, field

import pandas as pd

from .predictions import (
    AWAITING_RESULT,
    FINAL,
    IN_THEATERS,
    UPCOMING,
    Comparison,
    accuracy_stats,
)

FORECASTS = {"predicted": "Predictions", "projection": "Tracker Projection"}


def short_money(value: float) -> str:
    if abs(value) >= 1e9:
        return f"${value / 1e9:.2f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:,.0f}"


def _call(row: pd.Series, forecast: str) -> str:
    return (
        f"{row['title']}, predicted {short_money(row[forecast])} vs "
        f"{short_money(row['actual'])} actual ({row[f'{forecast}_pct_error']:+,.1f}%)"
    )


def _join(items: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _names(titles: list[str], limit: int = 5) -> str:
    shown = list(titles[:limit])
    if len(titles) > limit:
        shown.append(f"{len(titles) - limit} more")
    return _join(shown)


def _days_text(days) -> str:
    if pd.isna(days):
        return "in theaters"
    return "on opening day" if days == 0 else f"after {days} day{'' if days == 1 else 's'}"


def _status_overview(movies: pd.DataFrame) -> str:
    def count(status: str) -> int:
        return int((movies["run_status"] == status).sum())

    sentence = f"{count(FINAL)} of the {len(movies)} matched movies have finished their run and are scored"
    waiting = [
        f"{n} {label}"
        for n, label in [
            (count(IN_THEATERS), "still in theaters"),
            (count(UPCOMING), "not released yet"),
            (count(AWAITING_RESULT), "finished but missing a result"),
        ]
        if n
    ]
    return sentence + (f". Not scored yet: {_join(waiting)}." if waiting else ".")


def _in_theaters(movies: pd.DataFrame) -> pd.DataFrame:
    """Movies still in theaters with the % of their prediction reached so far, furthest along first."""
    showing = movies[movies["run_status"] == IN_THEATERS]
    showing = showing.assign(reached=showing["actual"] / showing["predicted"] * 100)
    return showing.sort_values("reached", ascending=False, na_position="last")


def _in_theaters_findings(movies: pd.DataFrame) -> list[str]:
    showing = _in_theaters(movies)
    if showing.empty:
        return []

    def describe(row) -> str:
        if pd.isna(row["actual"]):
            return f"{row['title']} ({_days_text(row['run_days'])}, no gross reported yet)"
        return (
            f"{row['title']} at {short_money(row['actual'])} {_days_text(row['run_days'])} "
            f"({row['reached']:.0f}% of its {short_money(row['predicted'])} prediction)"
        )

    descriptions = [describe(row) for _, row in showing.iterrows()]
    if len(descriptions) > 5:
        descriptions = descriptions[:5] + [f"and {len(descriptions) - 5} more (see the In Theaters sheet)"]
    findings = [f"Still in theaters, so not scored until their run ends: {'; '.join(descriptions)}."]

    passed = list(showing.loc[showing["actual"] > showing["predicted"], "title"])
    if passed:
        one = len(passed) == 1
        findings.append(
            f"{_join(passed)} {'has' if one else 'have'} already earned more than predicted, so "
            f"{'it was' if one else 'they were'} predicted too low whatever happens next."
        )
    return findings


def _upcoming_finding(movies: pd.DataFrame) -> str | None:
    upcoming = movies[(movies["run_status"] == UPCOMING) & movies["run_days"].notna()]
    if upcoming.empty:
        return None
    nxt = upcoming.loc[upcoming["run_days"].astype(int).idxmin()]
    days = int(nxt["run_days"])
    when = "opens today" if days == 0 else f"opens in {days} day{'' if days == 1 else 's'}"
    return f"Next release: {nxt['title']} {when}, predicted at {short_money(nxt['predicted'])}."


def prediction_findings(result: Comparison) -> list[str]:
    """Plain-English findings from fixed rules, so the same sheets always give the same text."""
    movies = result.movies
    scored = movies[movies["scored"]]
    findings = [_status_overview(movies)]

    stats = accuracy_stats(movies, "predicted")
    if stats is None:
        return findings + _progress_notes(movies) + _data_notes(result)

    n = stats["scored"]
    typical = f"The predictions were typically off by {stats['median_abs_pct']:.0f}% (median"
    if stats["mean_abs_pct"] - stats["median_abs_pct"] >= 10:
        typical += f"; the average is {stats['mean_abs_pct']:.0f}% because of a few big misses"
    findings.append(
        typical + f"). {stats['within_25']} of {n} landed within 25% of the actual result, "
        f"and {stats['within_10']} within 10%."
    )

    if stats["over"] != stats["under"]:
        lean = "high" if stats["over"] > stats["under"] else "low"
        findings.append(
            f"They ran {lean} more often than not: {stats['over']} too high vs {stats['under']} too low "
            f"(median error {stats['median_pct']:+.0f}%)."
        )
    total = (
        f"Added up, the predictions came to {short_money(stats['total_forecast'])} against "
        f"{short_money(stats['total_actual'])} actual ({stats['total_pct']:+.1f}%)"
    )
    if abs(stats["total_pct"]) < stats["median_abs_pct"] / 2:
        total += ", so misses in both directions largely cancelled out"
    findings.append(total + ".")

    errors = scored["predicted_pct_error"].dropna().abs()
    findings.append(f"Best call: {_call(scored.loc[errors.idxmin()], 'predicted')}.")
    findings.append(f"Biggest miss: {_call(scored.loc[errors.idxmax()], 'predicted')}.")

    tracker = accuracy_stats(movies, "projection")
    if tracker is not None:
        both = scored.dropna(subset=["predicted_pct_error", "projection_pct_error"])
        wins = int((both["predicted_pct_error"].abs() < both["projection_pct_error"].abs()).sum())
        findings.append(
            f"Against the tracker's own projections (typically off by {tracker['median_abs_pct']:.0f}%), "
            f"the predictions were closer on {wins} of {len(both)} movies."
        )
    return findings + _progress_notes(movies) + _data_notes(result)


def _progress_notes(movies: pd.DataFrame) -> list[str]:
    """Movies not scored yet: still in theaters, the next release, and finished runs missing a result."""
    notes = _in_theaters_findings(movies)
    upcoming = _upcoming_finding(movies)
    if upcoming:
        notes.append(upcoming)
    awaiting = list(movies.loc[movies["run_status"] == AWAITING_RESULT, "title"])
    if awaiting:
        one = len(awaiting) == 1
        notes.append(
            f"{len(awaiting)} {'movie has' if one else 'movies have'} finished {'its' if one else 'their'} run but "
            f"{'has' if one else 'have'} no result in the results sheet yet, so {'it isn' if one else 'they aren'}'t "
            f"scored: {_names(awaiting)}."
        )
    return notes


def _data_notes(result: Comparison) -> list[str]:
    """Notes about the input data: results restored from history, and titles that couldn't be matched."""
    findings = []
    restored = result.restored_from_history
    if restored:
        findings.append(
            f"{len(restored)} {'movie' if len(restored) == 1 else 'movies'} had no result in the results sheet "
            f"this time, so the last known result was used: {', '.join(restored)}."
        )
    unmatched_actuals, unmatched_predictions = len(result.unmatched_actuals), len(result.unmatched_predictions)
    if unmatched_actuals:
        names = ", ".join(result.unmatched_actuals[:5])
        if unmatched_actuals > 5:
            names += f" and {unmatched_actuals - 5} more"
        if unmatched_actuals == 1:
            findings.append(
                f"1 movie in the results sheet has no matching prediction "
                f"(it may be listed under a different title): {names}."
            )
        else:
            findings.append(
                f"{unmatched_actuals} movies in the results sheet have no matching prediction "
                f"(they may be listed under a different title): {names}."
            )
    if unmatched_predictions:
        findings.append(
            f"{unmatched_predictions} predicted {'movie doesn' if unmatched_predictions == 1 else 'movies don'}'t "
            "appear in the results sheet; see the Unmatched sheet."
        )
    return findings


# (name, description, upper limit of the absolute % error). Each scored movie lands in exactly one band.
ACCURACY_BANDS = [
    ("Nailed it", "within 10%", 10),
    ("Close", "10-25% off", 25),
    ("Off", "25-50% off", 50),
    ("Way off", "more than 50% off", float("inf")),
]


def accuracy_band(abs_pct: float) -> str:
    return next(name for name, _, limit in ACCURACY_BANDS if abs_pct <= limit)


def _movie_label(title: str, pct: float) -> str:
    return f"{title} ({pct:+.1f}%)" if abs(pct) < 10 else f"{title} ({pct:+,.0f}%)"


@dataclass
class ScorecardRow:
    measure: str
    result: str
    movies: list[str] = field(default_factory=list)


def scorecard(result: Comparison) -> list[ScorecardRow]:
    """Simple measures of the predictions' accuracy, each listing the movies behind it, followed by
    the movies that aren't scored yet (in theaters, upcoming, or missing a result)."""
    stats = accuracy_stats(result.movies, "predicted")
    rows = _accuracy_rows(result, stats) if stats is not None else []
    return rows + _not_scored_rows(result.movies)


def _not_scored_rows(movies: pd.DataFrame) -> list[ScorecardRow]:
    total = len(movies)
    rows = []

    showing = _in_theaters(movies)
    if not showing.empty:
        labels = [
            f"{r.title} (no gross yet, {_days_text(r.run_days)})"
            if pd.isna(r.actual)
            else f"{r.title} ({short_money(r.actual)} {_days_text(r.run_days)}, {r.reached:.0f}% of prediction)"
            for r in showing.itertuples()
        ]
        rows.append(ScorecardRow("Still in theaters (not scored yet)", f"{len(showing)} of {total}", labels))

    upcoming = movies[movies["run_status"] == UPCOMING].sort_values(["run_days", "release_date"], na_position="last")
    if not upcoming.empty:
        labels = [
            r.title if pd.isna(r.run_days) else f"{r.title} ({'opens today' if r.run_days == 0 else f'in {r.run_days} days'})"
            for r in upcoming.itertuples()
        ]
        rows.append(ScorecardRow("Upcoming", f"{len(upcoming)} of {total}", labels))

    awaiting = list(movies.loc[movies["run_status"] == AWAITING_RESULT, "title"])
    if awaiting:
        rows.append(ScorecardRow("Finished, but no result in the sheet yet", f"{len(awaiting)} of {total}", awaiting))
    return rows


def _accuracy_rows(result: Comparison, stats: dict) -> list[ScorecardRow]:
    scored = result.movies[result.movies["predicted_pct_error"].notna()].copy()
    scored["abs_error"] = scored["predicted_pct_error"].abs()
    n = len(scored)

    def labels(movies: pd.DataFrame) -> list[str]:
        return [_movie_label(t, p) for t, p in zip(movies["title"], movies["predicted_pct_error"], strict=True)]

    rows = [
        ScorecardRow("Finished and scored", f"{n} of {len(result.movies)} matched movies"),
        ScorecardRow("Typical miss (median)", f"{stats['median_abs_pct']:.0f}%"),
    ]
    scored["band"] = scored["abs_error"].map(accuracy_band)
    for name, description, _ in ACCURACY_BANDS:
        in_band = scored[scored["band"] == name].sort_values("abs_error")
        rows.append(ScorecardRow(f"{name} ({description})", f"{len(in_band)} of {n}", labels(in_band)))

    too_high = scored[scored["predicted_pct_error"] > 0].sort_values("predicted_pct_error", ascending=False)
    too_low = scored[scored["predicted_pct_error"] < 0].sort_values("predicted_pct_error")
    rows.append(ScorecardRow("Predicted too high", f"{len(too_high)} of {n}", labels(too_high)))
    rows.append(ScorecardRow("Predicted too low", f"{len(too_low)} of {n}", labels(too_low)))

    tracker = accuracy_stats(result.movies, "projection")
    if tracker is not None:
        both = scored[scored["projection_pct_error"].notna()]
        closer = both[both["abs_error"] < both["projection_pct_error"].abs()].sort_values("abs_error")
        rows.append(ScorecardRow("Closer than the tracker", f"{len(closer)} of {len(both)}", labels(closer)))
        rows.append(ScorecardRow("Tracker's typical miss (median)", f"{tracker['median_abs_pct']:.0f}%"))

    rows.append(
        ScorecardRow(
            "Total predicted vs actual",
            f"{short_money(stats['total_forecast'])} vs {short_money(stats['total_actual'])} ({stats['total_pct']:+.1f}%)",
        )
    )
    return rows


def detailed_stats_rows(result: Comparison) -> tuple[list[tuple[str, list, str]], list[str]]:
    """Returns ((label, [value per forecast], kind) rows, forecast column headers): the full
    side-by-side stats for every forecaster."""
    stats = {f: accuracy_stats(result.movies, f) for f in FORECASTS}
    stats = {f: s for f, s in stats.items() if s is not None}

    def row(label: str, key: str, kind: str):
        return label, [stats[f][key] for f in stats], kind

    rows = [
        row("Movies scored", "scored", "count"),
        row("Median error (either direction)", "median_abs_pct", "pct_abs"),
        row("Average error (either direction)", "mean_abs_pct", "pct_abs"),
        row("Median lean (+ = too high)", "median_pct", "pct"),
        row("Too high", "over", "count"),
        row("Too low", "under", "count"),
        row("Within 10% of actual", "within_10", "count"),
        row("Within 25% of actual", "within_25", "count"),
        row("Total forecast", "total_forecast", "money"),
        row("Total actual", "total_actual", "money"),
        row("Total difference", "total_pct", "pct"),
    ]
    return [(label, values, kind) for label, values, kind in rows], [FORECASTS[f] for f in stats]


def format_value(value: float, kind: str) -> str:
    if kind == "money":
        return short_money(value)
    if kind == "pct":
        return f"{value:+.1f}%"
    if kind == "pct_abs":
        return f"{value:.1f}%"
    return f"{value:,.0f}"
