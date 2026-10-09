import pandas as pd
import pytest

from src.predictions import (
    accuracy_stats,
    clean_title,
    compare,
    match_titles,
    parse_title_map,
    read_actuals,
    read_predictions,
    title_key,
)


class TestTitles:
    def test_clean_title_drops_emojis_and_stray_symbols(self):
        assert clean_title("Dune Part Three * ⛱️") == "Dune Part Three"
        assert clean_title("NARNIA:      THE MAGICIAN'S NEPHEW") == "NARNIA: THE MAGICIAN'S NEPHEW"

    def test_title_key_ignores_case_and_punctuation(self):
        assert title_key("Spider-Man: Brand New Day") == title_key("SPIDER-MAN BRAND NEW DAY")
        assert title_key("Fast & Furious") == title_key("Fast and Furious")


class TestMatchTitles:
    def test_exact_matches_ignore_emojis_and_case(self):
        assert match_titles(["TOY STORY 5", "MICHAEL"], [clean_title("Michael 🎤"), "Toy Story 5"]) == {0: 1, 1: 0}

    def test_close_spellings_match(self):
        assert match_titles(["COYOTE V. ACME", "WEREWULF"], ["Werwulf", "Coyote vs. Acme"]) == {0: 1, 1: 0}

    def test_different_numbers_never_fuzzy_match(self):
        assert match_titles(["TOY STORY 5", "SCARY MOVIE"], ["Toy Story 4", "Scary Movie 6"]) == {}

    def test_each_actual_is_matched_at_most_once(self):
        assert match_titles(["Mercy", "MERCY"], ["Mercy"]) == {0: 0}

    def test_title_map_pairs_differently_named_movies(self):
        title_map = parse_title_map("Minions 3=MINIONS AND MONSTERS; Jumanji 3 = jumanji: open world")
        assert match_titles(["JUMANJI: OPEN WORLD", "MINIONS AND MONSTERS"], ["Minions 3", "Jumanji 3"], title_map) == {
            0: 1,
            1: 0,
        }

    def test_title_map_with_unknown_title_raises_friendly_error(self):
        with pytest.raises(ValueError, match='no movie called "Minions 4" in the results sheet'):
            match_titles(["MINIONS AND MONSTERS"], ["Minions 3"], [("Minions 4", "MINIONS AND MONSTERS")])


class TestParseTitleMap:
    def test_titles_can_contain_commas(self):
        assert parse_title_map("Good Luck, Have Fun=GOOD LUCK, HAVE FUN, DON'T DIE\n") == [
            ("Good Luck, Have Fun", "GOOD LUCK, HAVE FUN, DON'T DIE")
        ]

    def test_entry_without_equals_raises_friendly_error(self):
        with pytest.raises(ValueError, match="should look like Results Title=Predictions Title"):
            parse_title_map("Minions 3")


def _actuals_sheet():
    # Shaped like a real tracker: title rows above the header, a repeated column name, day-first dates.
    return pd.DataFrame(
        [
            [None, "09/10/2026 13:31:15", None, None, None, None],
            [None, "Movie", "Release Date", "Worldwide Proj.", "Worldwide Actual", "Worldwide Actual"],
            [None, "Mercy 👮🏻", "23/01/2026", "$69,543,796", "$54,709,856", "$54,709,856"],
            [None, "Send Help 🏝", "30/01/2026", "$103,054,657", "$94,041,481", "$94,041,481"],
            [None, "Dune Part Three * ⛱️", "18/12/2026", "$840,364,215", None, None],
        ],
        columns=["Unnamed: 0", "2026 Movie Predictions", "Unnamed: 2", "Unnamed: 3", "Unnamed: 4", "Unnamed: 5"],
    )


def _predictions_sheet():
    return pd.DataFrame(
        {
            "MOVIE TITLE": ["MERCY", "SEND HELP", "DUNE PART THREE", "MELANIA"],
            "WORLDWIDE TOTAL": ["$41,470,588.24", "$62,045,454.55", "$900,000,000.00", "$1,804,040.40"],
        }
    )


class TestReadSheets:
    def test_read_actuals_finds_header_under_title_rows(self):
        actuals = read_actuals(_actuals_sheet())
        assert list(actuals["title"]) == ["Mercy", "Send Help", "Dune Part Three"]
        assert actuals.loc[0, "actual"] == pytest.approx(54_709_856)
        assert actuals.loc[0, "projection"] == pytest.approx(69_543_796)
        assert actuals.loc[0, "release_date"] == pd.Timestamp("2026-01-23")
        assert pd.isna(actuals.loc[2, "actual"])

    def test_read_predictions(self):
        predictions = read_predictions(_predictions_sheet())
        assert predictions.loc[0, "title"] == "MERCY"
        assert predictions.loc[0, "predicted"] == pytest.approx(41_470_588.24)

    def test_missing_columns_raise_friendly_error(self):
        with pytest.raises(ValueError, match="Couldn't find a column for: predicted in the predictions sheet"):
            read_predictions(pd.DataFrame({"MOVIE TITLE": ["X"], "Notes": ["y"]}))


class TestCompare:
    def test_scores_released_movies_and_lists_the_rest(self):
        result = compare(read_predictions(_predictions_sheet()), read_actuals(_actuals_sheet()))
        movies = result.movies.set_index("title")

        assert list(result.movies["title"]) == ["Mercy", "Send Help", "Dune Part Three"]
        assert movies.loc["Mercy", "predicted_pct_error"] == pytest.approx((41_470_588.24 - 54_709_856) / 54_709_856 * 100)
        assert movies.loc["Mercy", "projection_pct_error"] == pytest.approx((69_543_796 - 54_709_856) / 54_709_856 * 100)
        assert not movies.loc["Dune Part Three", "released"]
        assert pd.isna(movies.loc["Dune Part Three", "predicted_pct_error"])
        assert result.unmatched_predictions == ["MELANIA"]
        assert result.unmatched_actuals == []
        assert result.has_projection


class TestAccuracyStats:
    def test_summary_numbers(self):
        movies = pd.DataFrame(
            {
                "predicted": [110.0, 80.0, 300.0, 50.0],
                "actual": [100.0, 100.0, 100.0, None],
                "predicted_pct_error": [10.0, -20.0, 200.0, None],
            }
        )
        stats = accuracy_stats(movies, "predicted")
        assert stats["scored"] == 3
        assert stats["median_abs_pct"] == pytest.approx(20.0)
        assert stats["mean_abs_pct"] == pytest.approx(230 / 3)
        assert stats["median_pct"] == pytest.approx(10.0)
        assert (stats["over"], stats["under"]) == (2, 1)
        assert (stats["within_10"], stats["within_25"]) == (1, 2)
        assert stats["total_pct"] == pytest.approx((490 - 300) / 300 * 100)

    def test_nothing_scored_returns_none(self):
        movies = pd.DataFrame({"predicted": [1.0], "actual": [None], "predicted_pct_error": [None]})
        assert accuracy_stats(movies, "predicted") is None
