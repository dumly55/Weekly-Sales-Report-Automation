"""Scores movie box-office predictions against actual results.

Reads two sheets: one with predictions, and one with actual results (which may also carry its own
projections, e.g. a box-office tracker's). Movies are matched across the sheets by title, and each
forecast is scored against the actual worldwide gross once the movie has one.
"""

import difflib
import re
from dataclasses import dataclass
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
}

FUZZY_MATCH_CUTOFF = 0.85


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


def match_titles(prediction_titles: list[str], actual_titles: list[str]) -> dict[int, int]:
    """Returns {prediction index: actual index}, one-to-one. Exact key matches come first; the rest
    fall back to close spellings (e.g. "Coyote v. Acme" / "Coyote vs. Acme"), but only between titles
    with the same numbers, so "Toy Story 5" can never match "Toy Story 4"."""
    pred_keys = [title_key(t) for t in prediction_titles]
    actual_keys = [title_key(t) for t in actual_titles]
    matches: dict[int, int] = {}
    taken: set[int] = set()

    for p, key in enumerate(pred_keys):
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
    return out


@dataclass
class Comparison:
    movies: pd.DataFrame  # one row per matched movie
    unmatched_predictions: list[str]
    unmatched_actuals: list[str]
    has_projection: bool


def compare(predictions: pd.DataFrame, actuals: pd.DataFrame) -> Comparison:
    matches = match_titles(list(predictions["title"]), list(actuals["title"]))
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
            }
        )
    movies = pd.DataFrame(rows, columns=["title", "release_date", "predicted", "projection", "actual"])
    movies["released"] = movies["actual"].notna() & (movies["actual"] > 0)
    for forecast in ("predicted", "projection"):
        scored = movies["released"] & movies[forecast].notna()
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
    """Accuracy of one forecast column ("predicted" or "projection") over the released movies it covers.
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
