from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from src.download_data import _normalize_google_sheet_url, fetch_remote_dataset

RAW_COLUMNS = "InvoiceNo,StockCode,Description,Quantity,InvoiceDate,UnitPrice,CustomerID,Country"


class TestNormalizeGoogleSheetUrl:
    def test_rewrites_share_link_to_csv_export(self):
        url = "https://docs.google.com/spreadsheets/d/ABC123XYZ/edit#gid=456"
        result = _normalize_google_sheet_url(url)
        assert result == "https://docs.google.com/spreadsheets/d/ABC123XYZ/export?format=csv&gid=456"

    def test_defaults_to_gid_zero_when_absent(self):
        url = "https://docs.google.com/spreadsheets/d/ABC123XYZ/edit"
        result = _normalize_google_sheet_url(url)
        assert result == "https://docs.google.com/spreadsheets/d/ABC123XYZ/export?format=csv&gid=0"

    def test_leaves_non_google_sheets_urls_untouched(self):
        url = "https://example.com/data.csv"
        assert _normalize_google_sheet_url(url) == url

    def test_leaves_other_google_docs_urls_untouched(self):
        url = "https://docs.google.com/document/d/ABC123XYZ/edit"
        assert _normalize_google_sheet_url(url) == url


def _mock_response(content: bytes, content_type: str = "text/csv"):
    response = MagicMock()
    response.content = content
    response.headers = {"Content-Type": content_type}
    response.raise_for_status = MagicMock()
    return response


class TestFetchRemoteDataset:
    def test_parses_csv_link(self):
        csv_bytes = (RAW_COLUMNS + "\n536365,85123A,Widget,6,2011-11-22 08:26:00,2.55,17850.0,United Kingdom\n").encode()

        with patch("src.download_data.requests.get", return_value=_mock_response(csv_bytes, "text/csv")) as mock_get:
            df = fetch_remote_dataset("https://example.com/data.csv")

        mock_get.assert_called_once()
        assert list(df.columns) == RAW_COLUMNS.split(",")
        assert len(df) == 1

    def test_rewrites_and_fetches_google_sheet_link(self):
        csv_bytes = (RAW_COLUMNS + "\n536365,85123A,Widget,6,2011-11-22 08:26:00,2.55,17850.0,United Kingdom\n").encode()
        share_url = "https://docs.google.com/spreadsheets/d/ABC123/edit"

        with patch("src.download_data.requests.get", return_value=_mock_response(csv_bytes, "text/csv")) as mock_get:
            fetch_remote_dataset(share_url)

        called_url = mock_get.call_args[0][0]
        assert called_url == "https://docs.google.com/spreadsheets/d/ABC123/export?format=csv&gid=0"

    def test_missing_columns_raises_friendly_error(self):
        csv_bytes = b"Foo,Bar\n1,2\n"

        with patch("src.download_data.requests.get", return_value=_mock_response(csv_bytes, "text/csv")):
            with pytest.raises(ValueError, match="missing expected columns"):
                fetch_remote_dataset("https://example.com/data.csv")

    def test_unparseable_content_raises_friendly_error(self):
        with patch("src.download_data.requests.get", return_value=_mock_response(b"not a spreadsheet", "text/html")):
            with pytest.raises(ValueError, match="Couldn't read that link"):
                fetch_remote_dataset("https://example.com/not-a-file")

    def test_network_failure_raises_friendly_error(self):
        with patch("src.download_data.requests.get", side_effect=requests.exceptions.ConnectionError("boom")):
            with pytest.raises(requests.exceptions.RequestException, match="Couldn't download from that link"):
                fetch_remote_dataset("https://example.com/data.csv")

    def test_calls_status_callback(self):
        csv_bytes = (RAW_COLUMNS + "\n536365,85123A,Widget,6,2011-11-22 08:26:00,2.55,17850.0,United Kingdom\n").encode()
        messages = []

        with patch("src.download_data.requests.get", return_value=_mock_response(csv_bytes, "text/csv")):
            fetch_remote_dataset("https://example.com/data.csv", status_callback=messages.append)

        assert any("Downloading" in m for m in messages)
