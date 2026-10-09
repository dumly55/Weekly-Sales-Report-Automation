"""Scores movie predictions against actual results: runs the comparison, keeps the results history,
and writes the Excel report, Tableau CSVs and Markdown summary. Also the command-line entry point.

Usage:
    python -m src.prediction_report --predictions <link or file> --actuals <link or file>
"""

import argparse
import logging
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .history import (
    HISTORY_DB,
    fill_from_history,
    open_history,
    save_actuals,
    save_scores,
    write_history,
)
from .main import OUTPUT_DIR, describe_error, next_free_path, setup_logging
from .prediction_excel import build_prediction_workbook
from .prediction_findings import (
    FORECASTS,
    accuracy_band,
    detailed_stats_rows,
    format_value,
    prediction_findings,
    scorecard,
)
from .predictions import (
    IN_THEATERS,
    UPCOMING,
    Comparison,
    compare,
    load_actuals,
    load_source,
    parse_title_map,
    read_predictions,
)
from .sql_insights import Insight, run_insights

console = Console()


def tableau_rows(result: Comparison) -> pd.DataFrame:
    """Long, tidy table for Tableau: one row per movie per forecaster that made a forecast.

    `status` is the run status (Final, In theaters, Upcoming, Awaiting result). Error columns are
    only filled for Final movies; movies in theaters get their gross so far and progress instead."""
    frames = []
    for forecast, label in FORECASTS.items():
        movies = result.movies[result.movies[forecast].notna()]
        pct = movies[f"{forecast}_pct_error"]
        showing = movies["run_status"] == IN_THEATERS
        frames.append(
            pd.DataFrame(
                {
                    "movie": movies["title"],
                    "release_date": movies["release_date"].dt.strftime("%Y-%m-%d"),
                    "forecaster": label,
                    "forecast": movies[forecast],
                    "actual": movies["actual"].where(movies["scored"]),
                    "status": movies["run_status"],
                    "error": (movies[forecast] - movies["actual"]).where(movies["scored"]),
                    "pct_error": pct.round(2),
                    "abs_pct_error": pct.abs().round(2),
                    "direction": pct.map(lambda v: "" if pd.isna(v) else "Too high" if v > 0 else "Too low" if v < 0 else "Exact"),
                    "accuracy_band": pct.abs().map(lambda v: "" if pd.isna(v) else accuracy_band(v)),
                    "gross_so_far": movies["actual"].where(showing),
                    "pct_of_forecast_reached": (movies["actual"] / movies[forecast] * 100).where(showing).round(1),
                    "days_in_theaters": movies["run_days"].where(showing),
                    "days_until_release": movies["run_days"].where(movies["run_status"] == UPCOMING),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _markdown_table(table: pd.DataFrame) -> list[str]:
    def cell(value) -> str:
        return "" if pd.isna(value) else str(value).replace("|", "\\|")

    lines = ["| " + " | ".join(table.columns) + " |", "|" + "---|" * len(table.columns)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in table.itertuples(index=False)]
    return lines


def summary_markdown(
    result: Comparison, findings: list[str], as_of: date, insights: list[Insight] | None = None
) -> str:
    lines = [f"## Prediction Accuracy as of {as_of}", ""]
    rows = scorecard(result)
    if rows:
        lines += ["| Measure | Result | Movies |", "|---|---|---|"]
        for row in rows:
            movies = ", ".join(row.movies).replace("|", "\\|")
            lines.append(f"| **{row.measure}** | {row.result} | {movies} |")
    lines += ["", "### Key findings", ""] + [f"- {finding}" for finding in findings]

    stats, headers = detailed_stats_rows(result)
    if headers:
        lines += ["", "<details><summary>Detailed stats</summary>", ""]
        lines += ["| Metric | " + " | ".join(headers) + " |", "|---|" + "--:|" * len(headers)]
        for label, values, kind in stats:
            lines.append(f"| {label} | " + " | ".join(format_value(v, kind) for v in values) + " |")
        lines += ["", "</details>"]

    if insights:
        lines += ["", "<details><summary>SQL insights (queries in the sql/ folder)</summary>", ""]
        for insight in insights:
            lines += [f"**{insight.question}** (`{insight.name}.sql`)", ""]
            table = insight.display_table
            lines += (_markdown_table(table) if not table.empty else ["No rows yet."]) + [""]
        lines += ["</details>"]
    return "\n".join(lines) + "\n"


@dataclass
class PredictionRun:
    result: Comparison
    findings: list[str]
    out_path: Path  # the Excel report; this run's Tableau CSV sits next to it with a .csv suffix
    insights: list[Insight]

    @property
    def history_csv(self) -> Path:
        """Every saved run's scores plus this one: the Tableau source for trends over time."""
        return self.out_path.parent / "prediction_history.csv"


def run(
    predictions_source: str,
    actuals_source: str,
    as_of: date,
    title_map: list[tuple[str, str]] | None = None,
    history_path: Path = HISTORY_DB,
    save_history: bool = False,
) -> PredictionRun:
    """Scores the predictions and writes the report. This run's actuals and scores are added to an
    in-memory copy of the results history (blank actuals are filled from it), and the SQL queries
    run on that copy; `save_history` writes it back to the history file."""
    log = logging.getLogger(__name__)
    actuals = load_actuals(actuals_source)
    predictions = read_predictions(load_source(predictions_source))

    with closing(open_history(history_path)) as conn:
        save_actuals(actuals, conn, as_of)
        actuals, restored = fill_from_history(actuals, conn)
        result = compare(predictions, actuals, title_map)
        result.restored_from_history = [t for t in restored if t in set(result.movies["title"])]
        scores = tableau_rows(result)
        save_scores(scores, conn, as_of)
        insights = run_insights(conn)
        history = pd.read_sql_query("SELECT * FROM movie_scores ORDER BY run_date, movie, forecaster", conn)
        if save_history:
            write_history(conn, history_path)
            log.info("Saved this run's actuals and %d scores to %s", len(scores), history_path)

    findings = prediction_findings(result)
    out_path = next_free_path(OUTPUT_DIR / f"prediction_accuracy_{as_of}.xlsx")
    build_prediction_workbook(result, findings, out_path, as_of, insights)
    output = PredictionRun(result, findings, out_path, insights)
    scores.to_csv(out_path.with_suffix(".csv"), index=False)
    history.to_csv(output.history_csv, index=False)
    log.info("Prediction report written to %s (+ .csv files for Tableau)", out_path)
    return output


def _print_summary(run_output: PredictionRun, as_of: date) -> None:
    result, findings, out_path = run_output.result, run_output.findings, run_output.out_path
    rows = scorecard(result)
    if rows:
        table = Table(title=f"Prediction Accuracy as of {as_of}", title_style="bold cyan", show_lines=True)
        table.add_column("Measure", style="bold")
        table.add_column("Result")
        table.add_column("Movies", ratio=1)
        for row in rows:
            table.add_row(escape(row.measure), escape(row.result), escape(", ".join(row.movies)))
        console.print()
        console.print(table)
    console.print("\n[bold cyan]Key findings[/bold cyan]")
    for finding in findings:
        console.print(f"  • {escape(finding)}")
    console.print(f"\n[bold green]Report written[/bold green] to [bold]{out_path}[/bold]")
    console.print(f"[bold green]Tableau-ready data[/bold green] in [bold]{out_path.with_suffix('.csv')}[/bold]")
    console.print(f"[bold green]Full history for Tableau[/bold green] in [bold]{run_output.history_csv}[/bold]\n")


def _parse_title_map(value: str) -> list[tuple[str, str]]:
    try:
        return parse_title_map(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Score movie box-office predictions against actual results.")
    parser.add_argument("--predictions", required=True, help="Link or file path for the predictions sheet.")
    parser.add_argument("--actuals", required=True, help="Link or file path for the actual-results sheet.")
    parser.add_argument(
        "--title-map",
        default=None,
        type=_parse_title_map,
        help='Optional: pair movies listed under different names, e.g. "Minions 3=MINIONS AND MONSTERS; '
        'Jumanji 3=JUMANJI: OPEN WORLD" (results title=predictions title, separated by semicolons).',
    )
    parser.add_argument(
        "--save-history",
        action="store_true",
        help="Also record this run's actual results in the results history database (used by the scheduled run).",
    )
    parser.add_argument("--markdown-summary", default=None, help="Optional: append the scorecard and findings as Markdown to this file.")
    args = parser.parse_args()

    as_of = date.today()
    setup_logging(as_of, "predictions")
    try:
        output = run(args.predictions, args.actuals, as_of, args.title_map, save_history=args.save_history)
        if args.markdown_summary:
            with open(args.markdown_summary, "a", encoding="utf-8") as summary_file:
                summary_file.write(summary_markdown(output.result, output.findings, as_of, output.insights))
    except Exception as exc:
        logging.getLogger(__name__).exception("Prediction report failed")
        console.print(f"[bold red]Error:[/bold red] {describe_error(exc)}")
        sys.exit(1)
    _print_summary(output, as_of)


if __name__ == "__main__":
    main()
