from src.main import next_free_path


class TestNextFreePath:
    def test_returns_path_unchanged_when_free(self, tmp_path):
        path = tmp_path / "weekly_report_2011-11-28.xlsx"
        assert next_free_path(path) == path

    def test_adds_number_when_taken(self, tmp_path):
        path = tmp_path / "weekly_report_2011-11-28.xlsx"
        path.touch()
        assert next_free_path(path) == tmp_path / "weekly_report_2011-11-28 (2).xlsx"

    def test_skips_numbers_already_taken(self, tmp_path):
        path = tmp_path / "weekly_report_2011-11-28.xlsx"
        path.touch()
        (tmp_path / "weekly_report_2011-11-28 (2).xlsx").touch()
        assert next_free_path(path) == tmp_path / "weekly_report_2011-11-28 (3).xlsx"
