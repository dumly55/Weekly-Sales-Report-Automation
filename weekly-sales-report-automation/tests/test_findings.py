from datetime import date

import pandas as pd

from src.findings import build_findings
from src.report import build_report_data

# Report week: Mon 2024-09-09 to Sun 2024-09-15. Week before: 2024-09-02 to 2024-09-08.
AS_OF = date(2024, 9, 11)


def _row(order, product, qty, when, price, customer=1, country="United States"):
    return {
        "InvoiceNo": order,
        "StockCode": product,
        "Description": product,
        "Quantity": qty,
        "InvoiceDate": pd.Timestamp(when),
        "UnitPrice": price,
        "CustomerID": customer,
        "Country": country,
        "LineTotal": qty * price,
    }


def _findings(rows, cancellations=(), as_of=AS_OF):
    sales = pd.DataFrame(rows)
    cancels = pd.DataFrame(list(cancellations), columns=sales.columns)
    data = build_report_data(sales, cancels, as_of, currency="$")
    return build_findings(sales, data)


def _has(findings, text):
    return any(text in f for f in findings)


class TestHeadline:
    def test_rise(self):
        findings = _findings([_row("1", "Mug", 1, "2024-09-03", 100.0), _row("2", "Mug", 1, "2024-09-15", 150.0)])
        assert findings[0] == "Revenue rose 50.0% week over week, from $100.00 to $150.00."

    def test_fall(self):
        findings = _findings([_row("1", "Mug", 1, "2024-09-03", 200.0), _row("2", "Mug", 1, "2024-09-15", 150.0)])
        assert findings[0] == "Revenue fell 25.0% week over week, from $200.00 to $150.00."

    def test_small_change_is_flat(self):
        findings = _findings([_row("1", "Mug", 1, "2024-09-03", 100.0), _row("2", "Mug", 1, "2024-09-15", 100.2)])
        assert findings[0] == "Revenue was flat week over week at $100.20 (+0.2%)."

    def test_no_previous_week(self):
        findings = _findings([_row("1", "Mug", 1, "2024-09-15", 100.0)])
        assert findings[0].endswith("There were no sales the week before to compare against.")

    def test_empty_week(self):
        findings = _findings([_row("1", "Mug", 1, "2024-09-03", 100.0)], as_of=date(2024, 9, 18))
        assert findings == ["There were no sales in the week of 2024-09-16 to 2024-09-22."]


class TestPartialWeek:
    def test_warns_when_data_ends_mid_week(self):
        findings = _findings([_row("1", "Mug", 1, "2024-09-03", 100.0), _row("2", "Mug", 1, "2024-09-11", 50.0)])
        assert _has(findings, "the data only runs through Wednesday 2024-09-11 (3 of 7 days this week)")

    def test_silent_for_a_complete_week(self):
        findings = _findings(
            [_row("1", "Mug", 1, "2024-09-03", 100.0), _row("2", "Mug", 1, "2024-09-11", 50.0)], as_of=date(2024, 9, 4)
        )
        assert not _has(findings, "only runs through")


class TestBenchmark:
    def test_compares_with_average_of_previous_four_weeks(self):
        rows = [_row(str(i), "Mug", 1, day, 100.0) for i, day in enumerate(["2024-08-12", "2024-08-19", "2024-08-26", "2024-09-02"])]
        findings = _findings(rows + [_row("9", "Mug", 1, "2024-09-15", 150.0)])
        assert "That's 50.0% above the average of the 4 weeks before it ($100.00)." in findings

    def test_needs_at_least_two_previous_weeks(self):
        findings = _findings([_row("1", "Mug", 1, "2024-09-03", 100.0), _row("2", "Mug", 1, "2024-09-15", 150.0)])
        assert not _has(findings, "weeks before it")


class TestOrdersAndBasket:
    def test_bigger_orders_drove_the_change(self):
        rows = [
            _row("1", "Mug", 1, "2024-09-03", 50.0),
            _row("2", "Mug", 1, "2024-09-04", 50.0),
            _row("3", "Mug", 1, "2024-09-14", 100.0),
            _row("4", "Mug", 1, "2024-09-15", 100.0),
        ]
        assert _has(_findings(rows), "so the change came mostly from the size of each order.")

    def test_more_orders_drove_the_change(self):
        rows = [_row("1", "Mug", 1, "2024-09-03", 50.0)] + [_row(str(i), "Mug", 1, "2024-09-15", 50.0) for i in range(2, 6)]
        findings = _findings(rows)
        assert _has(findings, "Orders went from 1 to 4 (+300.0%)")
        assert _has(findings, "so the change came mostly from the number of orders.")


class TestDrivers:
    def test_names_the_country_behind_the_change(self):
        rows = [
            _row("1", "Mug", 1, "2024-09-03", 100.0, country="United Kingdom"),
            _row("2", "Pen", 1, "2024-09-03", 100.0, country="France"),
            _row("3", "Mug", 1, "2024-09-15", 300.0, country="United Kingdom"),
            _row("4", "Pen", 1, "2024-09-15", 100.0, country="France"),
        ]
        assert "United Kingdom drove 100% of the revenue increase (from $100.00 to $300.00, +200.0%)." in _findings(rows)

    def test_no_country_named_when_change_is_spread_out(self):
        rows = []
        for i, country in enumerate(["United Kingdom", "France", "Germany"]):
            rows += [
                _row(f"a{i}", "Mug", 1, "2024-09-03", 100.0, country=country),
                _row(f"b{i}", "Mug", 1, "2024-09-15", 150.0, country=country),
            ]
        findings = _findings(rows)
        assert not any(c in f for f in findings for c in ["United Kingdom drove", "France drove", "Germany drove"])

    def test_names_a_new_product_without_a_percentage(self):
        rows = [
            _row("1", "Mug", 1, "2024-09-03", 100.0),
            _row("2", "Mug", 1, "2024-09-15", 100.0),
            _row("3", "Lamp", 1, "2024-09-15", 80.0),
        ]
        assert "Lamp drove 100% of the revenue increase (from $0.00 to $80.00)." in _findings(rows)

    def test_group_that_outweighs_the_total_change(self):
        rows = [
            _row("1", "Mug", 1, "2024-09-03", 100.0),
            _row("2", "Pen", 1, "2024-09-03", 100.0),
            _row("3", "Mug", 1, "2024-09-15", 20.0),
            _row("4", "Pen", 1, "2024-09-15", 130.0),
        ]
        assert _has(_findings(rows), "Mug alone more than accounted for the revenue decrease (from $100.00 to $20.00, -80.0%).")


class TestBestSellerAndDay:
    def test_best_seller_and_top_ten_share(self):
        rows = [_row(str(i), f"Product {i}", 1, "2024-09-15", 10.0) for i in range(11)]
        rows.append(_row("big", "Lamp", 1, "2024-09-15", 90.0))
        findings = _findings(rows)
        assert _has(findings, "Best seller: Lamp ($90.00, 45% of revenue). The top 10 products made up 90% of revenue.")

    def test_top_ten_share_skipped_with_few_products(self):
        findings = _findings([_row("1", "Mug", 1, "2024-09-15", 10.0)])
        assert _has(findings, "Best seller: Mug ($10.00, 100% of revenue).")
        assert not _has(findings, "top 10")

    def test_strongest_day(self):
        rows = [_row("1", "Mug", 1, "2024-09-10", 30.0), _row("2", "Mug", 1, "2024-09-12", 70.0)]
        assert "The strongest day was Thursday 2024-09-12 ($70.00, 70% of the week's revenue)." in _findings(rows)


class TestCancellations:
    def test_reports_cancelled_orders_and_their_share(self):
        rows = [_row("1", "Mug", 1, "2024-09-15", 200.0), _row("2", "Mug", 1, "2024-09-15", 200.0)]
        cancels = [_row("C1", "Mug", -1, "2024-09-12", 10.0), _row("C2", "Pen", -2, "2024-09-13", 5.0)]
        assert "2 orders were cancelled, worth $20.00 (5.0% of revenue)." in _findings(rows, cancels)

    def test_calls_out_unusually_high_cancellations(self):
        rows = [_row("1", "Mug", 1, "2024-09-15", 200.0)]
        cancels = [_row("C1", "Mug", -1, "2024-09-12", 10.0), _row("C2", "Pen", -2, "2024-09-13", 5.0)]
        assert (
            "2 orders were cancelled, worth $20.00 (10.0% of revenue). "
            "That's unusually high: revenue after cancellations was $180.00."
        ) in _findings(rows, cancels)

    def test_silent_without_cancellations(self):
        assert not _has(_findings([_row("1", "Mug", 1, "2024-09-15", 200.0)]), "cancelled")
