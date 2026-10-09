"""Scores movie box-office predictions against actual results.

Reads two sheets: one with predictions, and one with actual results (which may also carry its own
projections, e.g. a box-office tracker's). Movies are matched across the sheets by title, and each
forecast is scored against the actual worldwide gross once the movie has one.
"""

import difflib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .download_data import fetch_remote_dataset, load_local_dataset
from .normalize import find_header, to_datetime, to_number

_TITLE_ALIASES = ["movietitle", "movie", "title", "film", "filmtitle", "name"]

PREDICTION_FIELDS = {
    "title": ("Title", _TITLE_ALIASES),
    "predicted": (
        "Predicted",
        ["worldwidetotal", "worldwideprediction", "predictedworldwide", "worldwideproj", "worldwideprojection",
         "worldwide", "prediction", "predicted", "projection", "forecast"],
    ),
}
ACTUAL_FIELDS = {
    "title": ("Title", _TITLE_ALIASES),
    "actual": ("Actual", ["worldwideactual", "actualworldwide", "actual", "actualgross", "worldwidegross"]),
    "projection": ("Projection", ["worldwideproj", "worldwideprojection", "projection", "projected", "prediction", "forecast"]),
    "release_date": ("Release Date", ["releasedate", "release", "date", "opening", "opendate"]),
    "status": (
        "Status",
        ["countdown", "status", "runstatus", "releasestatus", "boxofficestatus", "theatricalstatus", "stage"],
    ),
}

FUZZY_MATCH_CUTOFF = 0.85

# A movie's run status. Only finished runs are scored: a movie still in theaters has only its
# gross so far, which would make any full-run prediction look too high.
FINAL = "Final"  # run complete, with a result: scored
IN_THEATERS = "In theaters"  # still earning; its actual is the gross so far
UPCOMING = "Upcoming"  # not released yet
AWAITING_RESULT = "Awaiting result"  # run complete, but the results sheet has no number for it

_COMPLETE_WORDS = ("complete", "final", "closed", "ended", "finished")
_IN_THEATERS_WORDS = ("theater", "theatre", "in release", "now playing", "now showing", "release day", "opening day")


def classify_run(status_text: object, has_result: bool) -> tuple[str, int | None]:
    """Returns (run status, days) from a status cell like "✅ Run Complete", "🎬 In Theaters for
    21 days", "RELEASE DAY" or "70 Days". `days` is days in theaters for a movie in theaters, or
    days until release for an upcoming one. Without a recognizable status, a result counts as final."""
    text = "" if pd.isna(status_text) else str(status_text).lower()
    number = re.search(r"(\d+)\s*day", text)
    days = int(number.group(1)) if number else None
    if any(word in text for word in _COMPLETE_WORDS):
        return (FINAL if has_result else AWAITING_RESULT), None
    if any(word in text for word in _IN_THEATERS_WORDS):
        return IN_THEATERS, days if days is not None else 0
    if days is not None or "upcoming" in text or "coming soon" in text:
        return UPCOMING, days
    return (FINAL if has_result else UPCOMING), None


def load_source(source: str) -> pd.DataFrame:
    """Reads a sheet from a link (CSV/Excel or a public Google Sheet) or a local file path."""
    if source.lower().startswith(("http://", "https://")):
        return fetch_remote_dataset(source)
    return load_local_dataset(Path(source))


def clean_title(title: object) -> str:
    """Drops emojis and stray symbols (e.g. "Dune Part Three * ⛱️" -> "Dune Part Three")."""
    text = re.sub(r"[^\w\s:'’&.,!?()\-]", "", str(title))
    return re.sub(r"\s+", " ", text).strip()


def title_key(title: str) -> str:
    """Comparison key that ignores case, spacing and punctuation."""
    return re.sub(r"[^a-z0-9]", "", title.lower().replace("&", "and"))


def parse_title_map(text: str) -> list[tuple[str, str]]:
    """Parses "Minions 3=MINIONS AND MONSTERS; Jumanji 3=JUMANJI: OPEN WORLD" into
    (results title, predictions title) pairs. Pairs are split by semicolons or newlines,
    not commas, since titles can contain commas."""
    pairs = []
    for entry in re.split(r"[;\n]", text):
        if not entry.strip():
            continue
        actual, sep, predicted = entry.partition("=")
        if not sep or not actual.strip() or not predicted.strip():
            raise ValueError(f'Title map entry "{entry.strip()}" should look like Results Title=Predictions Title.')
        pairs.append((actual.strip(), predicted.strip()))
    return pairs


def match_titles(
    prediction_titles: list[str], actual_titles: list[str], title_map: list[tuple[str, str]] | None = None
) -> dict[int, int]:
    """Returns {prediction index: actual index}, one-to-one. Pairs from `title_map` (results title,
    predictions title) come first, then exact key matches; the rest fall back to close spellings
    (e.g. "Coyote v. Acme" / "Coyote vs. Acme"), but only between titles with the same numbers, so
    "Toy Story 5" can never match "Toy Story 4"."""
    pred_keys = [title_key(t) for t in prediction_titles]
    actual_keys = [title_key(t) for t in actual_titles]
    matches: dict[int, int] = {}
    taken: set[int] = set()

    for actual_name, predicted_name in title_map or []:
        a = next((i for i, k in enumerate(actual_keys) if k == title_key(actual_name)), None)
        p = next((i for i, k in enumerate(pred_keys) if k == title_key(predicted_name)), None)
        if a is None:
            raise ValueError(f'Title map: no movie called "{actual_name}" in the results sheet.')
        if p is None:
            raise ValueError(f'Title map: no movie called "{predicted_name}" in the predictions sheet.')
        matches[p] = a
        taken.add(a)

    for p, key in enumerate(pred_keys):
        if p in matches:
            continue
        for a, other in enumerate(actual_keys):
            if a not in taken and key and key == other:
                matches[p] = a
                taken.add(a)
                break

    for p, key in enumerate(pred_keys):
        if p in matches or not key:
            continue
        digits = re.findall(r"\d+", key)
        best, best_ratio = None, FUZZY_MATCH_CUTOFF
        for a, other in enumerate(actual_keys):
            if a in taken or re.findall(r"\d+", other) != digits:
                continue
            ratio = difflib.SequenceMatcher(None, key, other).ratio()
            if ratio >= best_ratio:
                best, best_ratio = a, ratio
        if best is not None:
            matches[p] = best
            taken.add(best)
    return matches


def _read(raw: pd.DataFrame, fields: dict, required: list[tuple[str, ...]], sheet_name: str) -> tuple[pd.DataFrame, dict]:
    df, mapping = find_header(raw.dropna(how="all"), fields, required)
    missing = [group[0] for group in required if not any(f in mapping for f in group)]
    if missing:
        raise ValueError(
            f"Couldn't find a column for: {', '.join(missing)} in the {sheet_name} sheet. "
            f"Its columns are: {', '.join(str(c) for c in df.columns)}."
        )
    df = df[df[mapping["title"]].notna()]
    df = df[df[mapping["title"]].map(clean_title) != ""]
    return df.reset_index(drop=True), mapping


def read_predictions(raw: pd.DataFrame) -> pd.DataFrame:
    df, mapping = _read(raw, PREDICTION_FIELDS, [("title",), ("predicted",)], "predictions")
    return pd.DataFrame({"title": df[mapping["title"]].map(clean_title), "predicted": to_number(df[mapping["predicted"]])})


def read_actuals(raw: pd.DataFrame) -> pd.DataFrame:
    df, mapping = _read(raw, ACTUAL_FIELDS, [("title",), ("actual",)], "actual results")
    out = pd.DataFrame({"title": df[mapping["title"]].map(clean_title), "actual": to_number(df[mapping["actual"]])})
    out["projection"] = to_number(df[mapping["projection"]]) if "projection" in mapping else float("nan")
    out["release_date"] = to_datetime(df[mapping["release_date"]]) if "release_date" in mapping else pd.NaT
    out["status_text"] = df[mapping["status"]].astype(object) if "status" in mapping else None
    return out


ACTUALS_DOWNLOADS = 3
SECONDS_BETWEEN_DOWNLOADS = 2.0


def load_actuals(source: str) -> pd.DataFrame:
    """Reads the actual-results sheet. A linked sheet is downloaded several times and any actual
    value seen in any download is kept: sheets that pull results live from a website can return
    some of those cells blank on one download and filled on the next."""
    actuals = read_actuals(load_source(source))
    if not source.lower().startswith(("http://", "https://")):
        return actuals

    for _ in range(ACTUALS_DOWNLOADS - 1):
        time.sleep(SECONDS_BETWEEN_DOWNLOADS)
        retry = read_actuals(load_source(source)).drop_duplicates("title").set_index("title")["actual"]
        blank = actuals["actual"].isna()
        actuals.loc[blank, "actual"] = actuals.loc[blank, "title"].map(retry)
    return actuals


@dataclass
class Comparison:
    movies: pd.DataFrame  # one row per matched movie
    unmatched_predictions: list[str]
    unmatched_actuals: list[str]
    has_projection: bool
    restored_from_history: list[str] = field(default_factory=list)  # blank in the sheet, last known result used


def compare(
    predictions: pd.DataFrame, actuals: pd.DataFrame, title_map: list[tuple[str, str]] | None = None
) -> Comparison:
    matches = match_titles(list(predictions["title"]), list(actuals["title"]), title_map)
    rows = []
    for p, a in matches.items():
        actual_row = actuals.iloc[a]
        rows.append(
            {
                "title": actual_row["title"],
                "release_date": actual_row["release_date"],
                "predicted": predictions.iloc[p]["predicted"],
                "projection": actual_row["projection"],
                "actual": actual_row["actual"],
                "status_text": actual_row.get("status_text"),
            }
        )
    columns = ["title", "release_date", "predicted", "projection", "actual", "status_text"]
    movies = pd.DataFrame(rows, columns=columns)
    has_result = movies["actual"].notna() & (movies["actual"] > 0)
    statuses = [classify_run(text, result) for text, result in zip(movies["status_text"], has_result)]
    movies["run_status"] = [status for status, _ in statuses]
    movies["run_days"] = pd.array([days for _, days in statuses], dtype="Int64")
    movies["scored"] = movies["run_status"] == FINAL
    for forecast in ("predicted", "projection"):
        scored = movies["scored"] & movies[forecast].notna()
        movies[f"{forecast}_pct_error"] = ((movies[forecast] - movies["actual"]) / movies["actual"] * 100).where(scored)
    movies = movies.sort_values(["release_date", "title"], na_position="last").reset_index(drop=True)

    matched_actuals = set(matches.values())
    return Comparison(
        movies=movies,
        unmatched_predictions=[t for i, t in enumerate(predictions["title"]) if i not in matches],
        unmatched_actuals=[t for i, t in enumerate(actuals["title"]) if i not in matched_actuals],
        has_projection=bool(actuals["projection"].notna().any()),
    )


def accuracy_stats(movies: pd.DataFrame, forecast: str) -> dict | None:
    """Accuracy of one forecast column ("predicted" or "projection") over the finished movies it covers.
    Errors are relative to the actual result: positive means the forecast was too high."""
    pct = movies[f"{forecast}_pct_error"].dropna()
    if pct.empty:
        return None
    scored = movies.loc[pct.index]
    total_forecast, total_actual = scored[forecast].sum(), scored["actual"].sum()
    # Medians lead because one wild miss (e.g. +1,300%) can dominate a mean.
    return {
        "scored": len(pct),
        "mean_abs_pct": float(pct.abs().mean()),
        "median_abs_pct": float(pct.abs().median()),
        "median_pct": float(pct.median()),
        "over": int((pct > 0).sum()),
        "under": int((pct < 0).sum()),
        "within_10": int((pct.abs() <= 10).sum()),
        "within_25": int((pct.abs() <= 25).sum()),
        "total_forecast": float(total_forecast),
        "total_actual": float(total_actual),
        "total_pct": float((total_forecast - total_actual) / total_actual * 100),
    }
