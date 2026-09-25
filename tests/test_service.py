import asyncio
import unittest
from datetime import date
from unittest import mock

from monarch_money_cli import service
from monarch_money_cli.client import AuthRequired, MonarchClient
from tests._mock import TOKEN, MockMonarch


class DateRange(unittest.TestCase):
    def test_default_is_current_month(self):
        self.assertEqual(service.date_range(None, None, today=date(2026, 2, 14)), ("2026-02-01", "2026-02-28"))

    def test_rejects_partial_bad_and_reversed(self):
        for s, e in [("2026-01-01", None), ("2026-13-01", "2026-12-01"), ("2026-02-01", "2026-01-01"), ("x", "y")]:
            with self.assertRaises(ValueError):
                service.date_range(s, e)


class PeriodRange(unittest.TestCase):
    def r(self, today=date(2026, 9, 25), **kw):
        return service.period_range(today=today, **kw)

    def test_default_and_positional_match_date_range(self):
        self.assertEqual(self.r(), ("2026-09-01", "2026-09-30"))
        self.assertEqual(service.period_range("2026-01-01", "2026-06-30"), ("2026-01-01", "2026-06-30"))

    def test_month(self):
        self.assertEqual(self.r(month="2026-02"), ("2026-02-01", "2026-02-28"))
        self.assertEqual(self.r(month="2024-02"), ("2024-02-01", "2024-02-29"))  # leap year
        self.assertEqual(self.r(month="1900-02"), ("1900-02-01", "1900-02-28"))  # century, not leap
        self.assertEqual(self.r(month="2000-02"), ("2000-02-01", "2000-02-29"))  # 400-year leap
        self.assertEqual(self.r(month="2026-12"), ("2026-12-01", "2026-12-31"))
        for bad in ("2026-13", "2026-00", "2026-1", "26-01", "2026", "2026-01-01", "+026-01", "abcd-ef"):
            with self.assertRaises(ValueError, msg=bad):
                self.r(month=bad)

    def test_year(self):
        self.assertEqual(self.r(year="2024"), ("2024-01-01", "2024-12-31"))
        for bad in ("24", "20245", "0000", "20x4", "+202"):
            with self.assertRaises(ValueError, msg=bad):
                self.r(year=bad)

    def test_ytd(self):
        self.assertEqual(self.r(ytd=True), ("2026-01-01", "2026-09-25"))
        self.assertEqual(self.r(ytd=True, today=date(2026, 1, 1)), ("2026-01-01", "2026-01-01"))
        self.assertEqual(self.r(ytd=True, today=date(2024, 12, 31)), ("2024-01-01", "2024-12-31"))

    def test_last_month(self):
        self.assertEqual(self.r(last_month=True), ("2026-08-01", "2026-08-31"))
        self.assertEqual(self.r(last_month=True, today=date(2026, 1, 15)), ("2025-12-01", "2025-12-31"))
        self.assertEqual(self.r(last_month=True, today=date(2024, 3, 31)), ("2024-02-01", "2024-02-29"))
        self.assertEqual(self.r(last_month=True, today=date(2026, 3, 1)), ("2026-02-01", "2026-02-28"))

    def test_days(self):
        self.assertEqual(self.r(days=1), ("2026-09-25", "2026-09-25"))
        self.assertEqual(self.r(days=7), ("2026-09-19", "2026-09-25"))
        self.assertEqual(self.r(days=3, today=date(2026, 1, 2)), ("2025-12-31", "2026-01-02"))
        self.assertEqual(self.r(days=2, today=date(2024, 3, 1)), ("2024-02-29", "2024-03-01"))
        for bad in (0, -5, service.MAX_DAYS + 1):
            with self.assertRaises(ValueError, msg=bad):
                self.r(days=bad)

    def test_from_to(self):
        self.assertEqual(self.r(from_="2026-09-01"), ("2026-09-01", "2026-09-25"))
        self.assertEqual(self.r(from_="2024-02-29", to="2024-03-01"), ("2024-02-29", "2024-03-01"))
        for kw in ({"from_": "2026-10-01"}, {"from_": "2026-09-10", "to": "2026-09-01"}, {"from_": "2026-02-30"}):
            with self.assertRaises(ValueError, msg=kw):
                self.r(**kw)
        with self.assertRaises(service.DateUsageError):
            self.r(to="2026-09-01")

    def test_options_are_exclusive(self):
        for kw in ({"month": "2026-01", "year": "2026"}, {"ytd": True, "last_month": True},
                   {"days": 7, "from_": "2026-09-01"}, {"start": "2026-01-01", "end": "2026-01-31", "ytd": True},
                   {"start": "2026-01-01", "month": "2026-01"}, {"to": "2026-09-01", "days": 3}):
            with self.assertRaises(service.DateUsageError, msg=kw):
                self.r(**kw)


class Reads(unittest.TestCase):
    def run_(self, coro):
        return asyncio.run(coro)

    def test_accounts_trimmed_and_hidden_filtered(self):
        with MockMonarch():
            c = MonarchClient(TOKEN)
            d = self.run_(service.accounts(c))
            self.assertEqual([a["name"] for a in d["accounts"]], ["Checking"])
            self.assertNotIn("mask", d["accounts"][0])
            self.assertEqual(self.run_(service.accounts(c, include_hidden=True))["count"], 2)

    def test_transactions_exclude_notes(self):
        with MockMonarch() as m:
            d = self.run_(service.transactions(MonarchClient(TOKEN), "2026-09-01", "2026-09-30", limit=2))
            self.assertEqual(d["returned"], 2)
            self.assertTrue(d["has_more"])
            self.assertNotIn("notes", d["transactions"][0])
            self.assertNotIn("notes", m.requests[-1]["body"]["query"])

    def test_limit_bounds(self):
        for bad in (0, 101):
            with self.assertRaises(ValueError):
                self.run_(service.transactions(MonarchClient(TOKEN), "2026-09-01", "2026-09-30", limit=bad))

    def test_cashflow_and_spending(self):
        with MockMonarch():
            c = MonarchClient(TOKEN)
            self.assertEqual(self.run_(service.cashflow_summary(c, "2026-09-01", "2026-09-30"))["savings"], 4700)
            d = self.run_(service.spending_by_category(c, "2026-09-01", "2026-09-30"))
            self.assertEqual([x["category"] for x in d["categories"]], ["Rent", "Groceries"])
            self.assertEqual({x["group_type"] for x in d["categories"]}, {"expense"})
            self.assertEqual(d["total"], -1800)

    def test_income_largest_first(self):
        with MockMonarch():
            d = self.run_(service.income_by_category(MonarchClient(TOKEN), "2026-09-01", "2026-09-30"))
            self.assertEqual([x["category"] for x in d["categories"]], ["Paycheck", "Interest"])
            self.assertEqual(d["total"], 5012.5)

    def test_cashflow_breakdowns_in_one_call_without_transfers(self):
        with MockMonarch() as m:
            d = self.run_(service.cashflow_summary(MonarchClient(TOKEN), "2026-09-01", "2026-09-30"))
            self.assertEqual(len(m.requests), 1)
            self.assertEqual([x["category"] for x in d["income_categories"]], ["Paycheck", "Interest"])
            self.assertEqual([x["category"] for x in d["expense_categories"]], ["Rent", "Groceries"])
            self.assertNotIn("Credit Card Payment", str(d))

    def test_bad_token_raises_auth_required(self):
        with MockMonarch(), self.assertRaises(AuthRequired):
            self.run_(MonarchClient("b" * 64).accounts())


class Entities(unittest.TestCase):
    def run_(self, coro):
        return asyncio.run(coro)

    def resolve(self, *specs):
        with MockMonarch():
            return self.run_(service.resolve_entities(MonarchClient(TOKEN), list(specs)))

    def test_list_trimmed(self):
        with MockMonarch() as m:
            d = self.run_(service.entities(MonarchClient(TOKEN)))
            query = m.requests[-1]["body"]["query"]
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["entities"][0], {"id": "101", "name": "Acme LLC", "structure": "llc",
                                            "accounts_count": 1, "transactions_count": 40,
                                            "accounts": [{"id": "1", "name": "Checking"}]})
        for field in ("notes", "description", "logoUrl"):
            self.assertNotIn(field, query)
        self.assertNotIn("secret entity note", str(d))

    def test_resolve_by_id_name_prefix_and_household(self):
        self.assertIsNone(self.resolve())
        self.assertEqual(self.resolve("101"),
                         {"entity_ids": ["101"], "entity_names": ["Acme LLC"], "include_household": False})
        self.assertEqual(self.resolve("aCmE llc")["entity_ids"], ["101"])  # case-insensitive exact
        self.assertEqual(self.resolve("acme la")["entity_ids"], ["102"])  # unique prefix
        self.assertEqual(self.resolve("HOUSEHOLD"),
                         {"entity_ids": [], "entity_names": [], "include_household": True})
        both = self.resolve("101", "household", "Acme LLC")  # duplicates collapse
        self.assertEqual((both["entity_ids"], both["include_household"]), (["101"], True))

    def test_resolve_unknown_and_ambiguous_list_valid_names(self):
        for spec, word in (("999", "Unknown"), ("Globex", "Unknown"), ("", "Unknown"), ("acme", "ambiguous")):
            with self.assertRaises(ValueError) as cm:
                self.resolve(spec)
            self.assertIn(word, str(cm.exception))
            self.assertIn("'Acme LLC' (id 101)", str(cm.exception))

    def test_entity_set_always_has_include_unassigned(self):
        self.assertIsNone(service.entity_set(None))
        self.assertEqual(service.entity_set({"entity_ids": ["101"], "entity_names": ["x"], "include_household": False}),
                         {"businessEntityIds": ["101"], "includeUnassigned": False})
        self.assertEqual(service.entity_set({"entity_ids": [], "entity_names": [], "include_household": True}),
                         {"businessEntityIds": [], "includeUnassigned": True})

    def test_scope_forwarded_to_every_read(self):
        scope = {"entity_ids": ["101"], "entity_names": ["Acme LLC"], "include_household": True}
        want = {"businessEntityIds": ["101"], "includeUnassigned": True}
        with MockMonarch() as m:
            c = MonarchClient(TOKEN)
            outs = [self.run_(service.transactions(c, "2026-09-01", "2026-09-30", scope=scope)),
                    self.run_(service.cashflow_summary(c, "2026-09-01", "2026-09-30", scope)),
                    self.run_(service.spending_by_category(c, "2026-09-01", "2026-09-30", scope)),
                    self.run_(service.income_by_category(c, "2026-09-01", "2026-09-30", scope))]
            self.run_(service.accounts(c, scope=scope))
            filters = [r["body"]["variables"]["filters"] for r in m.requests]
        self.assertEqual([f["businessEntitySet"] for f in filters], [want] * 5)
        self.assertTrue(filters[-1]["includeHidden"])  # hidden accounts are filtered client-side
        self.assertTrue(all(o["entity_scope"] == scope for o in outs))

    def test_unscoped_reads_send_no_entity_filter(self):
        with MockMonarch() as m:
            c = MonarchClient(TOKEN)
            self.run_(service.transactions(c, "2026-09-01", "2026-09-30"))
            d = self.run_(service.accounts(c))
            self.assertNotIn("businessEntitySet", m.requests[0]["body"]["variables"]["filters"])
            self.assertNotIn("filters", m.requests[1]["body"]["variables"])
            self.assertNotIn("entity_scope", d)

    def test_scoped_accounts_still_hide_hidden(self):
        scope = {"entity_ids": [], "entity_names": [], "include_household": True}
        with MockMonarch():
            c = MonarchClient(TOKEN)
            self.assertEqual(self.run_(service.accounts(c, scope=scope))["count"], 1)
            self.assertEqual(self.run_(service.accounts(c, include_hidden=True, scope=scope))["count"], 2)

    def test_cashflow_by_entity(self):
        with MockMonarch() as m:
            d = self.run_(service.cashflow_by_entity(MonarchClient(TOKEN), "2026-09-01", "2026-09-30"))
            self.assertEqual(len(m.requests), 1)
            self.assertNotIn("businessEntitySet", m.requests[0]["body"]["variables"]["filters"])
        self.assertEqual([(r["entity_id"], r["entity"]) for r in d["entities"]],
                         [("101", "Acme LLC"), (None, "household")])  # household last
        self.assertEqual(d["total"], {"income": 5000, "expenses": -300, "savings": 4700,
                                      "savings_rate": 0.94, "transactions": 10})

    def test_negative_savings_rate_total_is_zero(self):
        summaries = {"businessEntitySummaries": [{"businessEntity": None, "summary": {
            "sumIncome": 100, "sumExpense": -300, "savings": -200, "savingsRate": 0, "count": 1}}]}
        with MockMonarch(), mock.patch.dict("tests._mock.ENTITY_SUMMARIES", summaries):
            d = self.run_(service.cashflow_by_entity(MonarchClient(TOKEN), "2026-09-01", "2026-09-30"))
        self.assertEqual(d["total"]["savings_rate"], 0.0)
