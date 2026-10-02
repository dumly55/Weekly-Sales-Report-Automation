import math

import pandas as pd
import pytest

from src.clean import clean_transactions
from src.normalize import match_columns, standardize

UCI_COLUMNS = ["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate", "UnitPrice", "CustomerID", "Country"]


class TestMatchColumns:
    def test_demo_dataset_columns_map_to_themselves(self):
        mapping = match_columns(UCI_COLUMNS)
        assert mapping == {
            "date": "InvoiceDate",
            "order_id": "InvoiceNo",
            "product_code": "StockCode",
            "product": "Description",
            "quantity": "Quantity",
            "unit_price": "UnitPrice",
            "customer": "CustomerID",
            "country": "Country",
        }

    def test_ignores_case_spaces_and_punctuation(self):
        mapping = match_columns(["ORDER-DATE", "unit_price", "Qty."])
        assert mapping == {"date": "ORDER-DATE", "unit_price": "unit_price", "quantity": "Qty."}

    def test_prefers_more_specific_names(self):
        mapping = match_columns(["Customer Name", "Customer ID", "Region", "Country"])
        assert mapping["customer"] == "Customer ID"
        assert mapping["country"] == "Country"

    def test_unrecognized_columns_are_left_out(self):
        assert match_columns(["Ship Mode", "Discount"]) == {}


class TestStandardize:
    def test_superstore_style_sheet(self):
        # Shaped like the widely used "Sample Superstore" dataset: no unit price, only a line total.
        sheet = pd.DataFrame(
            {
                "Row ID": [1, 2],
                "Order ID": ["CA-2016-152156", "CA-2016-152157"],
                "Order Date": ["11/8/2016", "11/9/2016"],
                "Ship Date": ["11/11/2016", "11/12/2016"],
                "Customer ID": ["CG-12520", "DV-13045"],
                "Country": ["United States", "United States"],
                "Product ID": ["FUR-BO-10001798", "OFF-LA-10000240"],
                "Product Name": ["Bookcase", "Labels"],
                "Sales": [261.96, 14.62],
                "Quantity": [2, 2],
            }
        )
        out, mapping = standardize(sheet)

        assert mapping["date"] == "Order Date"
        assert mapping["line_total"] == "Sales"
        assert list(out["UnitPrice"]) == pytest.approx([130.98, 7.31])

        sales, cancellations, _ = clean_transactions(out)
        assert len(cancellations) == 0, "CA- order IDs must not be mistaken for cancellations"
        assert sales["LineTotal"].sum() == pytest.approx(261.96 + 14.62)

    def test_minimal_sheet_with_only_date_and_amount(self):
        sheet = pd.DataFrame({"Date": ["2024-01-15", "2024-01-16"], "Amount": ["$10.00", "$20.00"]})
        out, _ = standardize(sheet)

        assert list(out["Quantity"]) == [1, 1]
        assert list(out["UnitPrice"]) == pytest.approx([10.0, 20.0])
        assert list(out["InvoiceNo"]) == ["2", "3"]
        assert list(out["Description"]) == ["Unknown product", "Unknown product"]
        assert list(out["Country"]) == ["Unknown", "Unknown"]
        assert all(math.isnan(v) for v in out["CustomerID"])

    def test_product_name_and_code_fill_in_for_each_other(self):
        sheet = pd.DataFrame({"Date": ["2024-01-15"], "Price": [5.0], "Item": ["Mug"]})
        out, _ = standardize(sheet)
        assert out.loc[0, "StockCode"] == "Mug"
        assert out.loc[0, "Description"] == "Mug"

    def test_missing_required_columns_raises_friendly_error(self):
        sheet = pd.DataFrame({"Foo": [1], "Bar": [2]})
        with pytest.raises(ValueError, match=r"Couldn't find a column for: date, unit_price \(or line_total\)\. The sheet's columns are: Foo, Bar"):
            standardize(sheet)
