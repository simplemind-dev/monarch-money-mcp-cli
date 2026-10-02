import asyncio
import unittest
from datetime import date
from unittest import mock

from monarch_money_cli import service
from monarch_money_cli.client import AuthRequired, MonarchClient, MonarchError
from tests._mock import ACCOUNTS, TOKEN, MockMonarch


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


class PlanningAndInvestments(unittest.TestCase):
    def run_(self, coro):
        return asyncio.run(coro)

    def test_trailing_year_default(self):
        self.assertEqual(service.period_range(today=date(2026, 9, 25), default="year"), ("2025-10-01", "2026-09-25"))
        self.assertEqual(service.trailing_year(date(2026, 1, 31)), ("2025-02-01", "2026-01-31"))
        self.assertEqual(service.period_range(today=date(2026, 9, 25), default="year", ytd=True),
                         ("2026-01-01", "2026-09-25"))

    def test_budgets_per_month_skip_transfers_and_empty(self):
        with MockMonarch() as m:
            d = self.run_(service.budgets(MonarchClient(TOKEN), "2026-09-01", "2026-09-30"))
        self.assertEqual(m.requests[-1]["body"]["variables"], {"startDate": "2026-09-01", "endDate": "2026-09-30"})
        self.assertEqual([x["month"] for x in d["months"]], ["2026-09"])
        sep = d["months"][0]
        self.assertEqual([c["category"] for c in sep["categories"]], ["Paycheck", "Rent", "Groceries"])
        self.assertEqual(sep["categories"][2], {"category": "Groceries", "group": "Living", "group_type": "expense",
                                                "budgeted": 400, "actual": 300, "remaining": 100})
        self.assertEqual(sep["expenses"], {"budgeted": 1900, "actual": 1800, "remaining": 100})

    def test_goals_sum_contributions_and_hide_archived(self):
        with MockMonarch() as m:
            c = MonarchClient(TOKEN)
            d = self.run_(service.goals(c, "2026-09-01", "2026-09-30"))
            self.assertEqual(d["goals"], [{"id": "gl1", "name": "Emergency fund", "priority": 1, "status": "active",
                                           "planned": 500, "contributed": 450}])
            self.assertNotIn("image", m.requests[-1]["body"]["query"])
            d = self.run_(service.goals(c, "2026-09-01", "2026-10-31", include_archived=True))
            self.assertEqual([(g["name"], g["status"], g["planned"]) for g in d["goals"]],
                             [("Emergency fund", "active", 1000), ("Old car", "archived", 0)])

    def test_recurring_sorted_with_status(self):
        with MockMonarch() as m:
            d = self.run_(service.recurring(MonarchClient(TOKEN), "2026-09-01", "2026-09-30"))
        self.assertNotIn("logoUrl", m.requests[-1]["body"]["query"])
        self.assertEqual([(i["merchant"], i["status"]) for i in d["items"]],
                         [("Netflix", "paid"), ("=cmd()", "missed"), ("Landlord", "upcoming")])
        self.assertTrue(d["items"][0]["approximate"])
        self.assertEqual(d["total"], -1575.49)

    def test_holdings_default_to_brokerage_accounts(self):
        brokerage = {"id": "3", "displayName": "Brokerage", "isHidden": False, "isAsset": True,
                     "currentBalance": 6500, "includeInNetWorth": True, "type": {"name": "brokerage"},
                     "subtype": {"name": "brokerage"}}
        with MockMonarch() as m, mock.patch.dict(ACCOUNTS, {"accounts": [*ACCOUNTS["accounts"], brokerage]}):
            d = self.run_(service.holdings(MonarchClient(TOKEN), today=date(2026, 9, 25)))
        self.assertEqual(m.requests[-1]["body"]["variables"]["input"],
                         {"accountIds": ["3"], "startDate": "2026-09-25", "endDate": "2026-09-25",
                          "includeHiddenHoldings": True})
        self.assertEqual(d["total_value"], 6500)
        self.assertEqual([h["ticker"] for h in d["holdings"]], [None, "VTI"])
        self.assertEqual(d["holdings"][1]["gain"], 500)
        self.assertIsNone(d["holdings"][0]["gain"])  # unknown cost basis
        self.assertEqual(d["holdings"][0]["name"], "Private fund")

    def test_holdings_without_investment_accounts_makes_no_portfolio_call(self):
        with MockMonarch() as m:
            d = self.run_(service.holdings(MonarchClient(TOKEN)))
        self.assertEqual(d["holdings"], [])
        self.assertNotIn("Web_GetHoldings", [r["body"]["operationName"] for r in m.requests])

    def test_net_worth_month_end_and_daily(self):
        with MockMonarch():
            c = MonarchClient(TOKEN)
            d = self.run_(service.net_worth(c, "2026-07-01", "2026-09-30"))
            self.assertEqual(d["points"], [{"date": "2026-07-31", "net_worth": 101000},
                                           {"date": "2026-08-31", "net_worth": 103000},
                                           {"date": "2026-09-10", "net_worth": 104500.5}])
            self.assertEqual((d["start_net_worth"], d["change"]), (101000, 3500.5))
            d = self.run_(service.net_worth(c, "2026-07-01", "2026-09-30", daily=True))
            self.assertEqual((len(d["points"]), d["change"]), (5, 4500.5))


class Writes(unittest.TestCase):
    def run_(self, coro):
        return asyncio.run(coro)

    def writer(self):
        return MonarchClient(TOKEN, allow_writes=True)

    def test_categories_trimmed(self):
        with MockMonarch():
            d = self.run_(service.categories(MonarchClient(TOKEN)))
        self.assertEqual(d["count"], 6)
        self.assertEqual(d["categories"][0], {"id": "204", "name": "Dining", "group": "Food",
                                              "group_type": "expense", "disabled": False})
        self.assertEqual(d["categories"][-1]["name"], "Paycheck")  # expense groups sort before income

    def test_resolve_category(self):
        with MockMonarch():
            c = MonarchClient(TOKEN)
            self.assertEqual(self.run_(service.resolve_category(c, "203"))["name"], "Restaurants")
            self.assertEqual(self.run_(service.resolve_category(c, " restaurants "))["id"], "203")
            for spec, msg in (("Restaur", "Unknown category"), ("DINING", "ambiguous"), ("Old stuff", "disabled")):
                with self.assertRaises(ValueError, msg=spec) as cm:
                    self.run_(service.resolve_category(c, spec))
                self.assertIn(msg, str(cm.exception))

    def test_set_category_preview_sends_no_mutation(self):
        with MockMonarch() as m:
            d = self.run_(service.set_transaction_category(MonarchClient(TOKEN), "301", "Restaurants"))
        self.assertEqual(m.mutations(), [])
        self.assertEqual((d["changed"], d["applied"]), (True, False))
        self.assertEqual(d["before"]["category"], {"id": "202", "name": "Groceries"})
        self.assertEqual(d["after"]["category"], {"id": "203", "name": "Restaurants"})
        self.assertEqual(d["transaction"], {"id": "301", "date": "2026-09-02", "amount": -42.1,
                                            "merchant": "King Soopers"})
        self.assertNotIn("secret note", str(d))

    def test_set_category_apply_sends_one_mutation(self):
        with MockMonarch() as m:
            d = self.run_(service.set_transaction_category(self.writer(), "301", "restaurants", apply=True))
        self.assertEqual([b["variables"] for b in m.mutations()], [{"input": {"id": "301", "category": "203"}}])
        self.assertEqual((d["changed"], d["applied"]), (True, True))
        self.assertEqual(d["after"]["category"], {"id": "203", "name": "Restaurants"})

    def test_set_category_no_change_skips_mutation(self):
        with MockMonarch() as m:
            d = self.run_(service.set_transaction_category(self.writer(), "301", "Groceries", apply=True))
        self.assertEqual(m.mutations(), [])
        self.assertEqual((d["changed"], d["applied"]), (False, False))
        self.assertIn("nothing to change", d["message"])

    def test_apply_needs_a_write_client(self):
        with MockMonarch() as m, self.assertRaises(MonarchError):
            self.run_(service.set_transaction_category(MonarchClient(TOKEN), "301", "Restaurants", apply=True))
        self.assertEqual(m.mutations(), [])

    def test_invalid_and_missing_transaction(self):
        with MockMonarch() as m:
            for bad in ("abc", "1; drop", "", "1" * 31, "-1"):
                with self.assertRaises(ValueError, msg=bad):
                    self.run_(service.set_transaction_category(self.writer(), bad, "Restaurants", apply=True))
            self.assertEqual(m.requests, [])
            with self.assertRaises(ValueError) as cm:
                self.run_(service.update_transaction_tags(self.writer(), "999", add=["Supplies"], apply=True))
            self.assertIn("not found", str(cm.exception))
            self.assertEqual(m.mutations(), [])

    def test_payload_errors_fail_the_write(self):
        with MockMonarch() as m:
            m.payload_errors = {"message": "Transaction is locked", "code": "LOCKED", "fieldErrors": []}
            with self.assertRaises(MonarchError) as cm:
                self.run_(service.set_transaction_category(self.writer(), "301", "Restaurants", apply=True))
        self.assertIn("Transaction is locked", str(cm.exception))

    def test_tags_merge_add_and_remove(self):
        with MockMonarch() as m:
            d = self.run_(service.update_transaction_tags(self.writer(), "301", add=["supplies"],
                                                          remove=["BUSINESS"], apply=True))
        self.assertEqual([b["variables"] for b in m.mutations()],
                         [{"input": {"transactionId": "301", "tagIds": ["g2"]}}])
        self.assertEqual(d["before"]["tags"], [{"id": "g1", "name": "Business"}])
        self.assertEqual(d["after"]["tags"], [{"id": "g2", "name": "Supplies"}])
        self.assertEqual((d["added"], d["removed"]), ([{"id": "g2", "name": "Supplies"}],
                                                      [{"id": "g1", "name": "Business"}]))
        self.assertTrue(d["applied"])

    def test_tags_add_keeps_existing(self):
        with MockMonarch() as m:
            d = self.run_(service.update_transaction_tags(MonarchClient(TOKEN), "301", add=["g2"]))
        self.assertEqual(m.mutations(), [])  # preview
        self.assertEqual([t["id"] for t in d["after"]["tags"]], ["g1", "g2"])
        self.assertEqual((d["changed"], d["applied"]), (True, False))

    def test_tags_no_change_skips_mutation(self):
        with MockMonarch() as m:
            d = self.run_(service.update_transaction_tags(self.writer(), "301", add=["Business"], apply=True))
            self.assertFalse(d["changed"])
            d = self.run_(service.update_transaction_tags(self.writer(), "301", remove=["Supplies"], apply=True))
            self.assertFalse(d["changed"])
        self.assertEqual(m.mutations(), [])

    def test_tags_bad_input(self):
        with MockMonarch() as m:
            for kw, msg in (({}, "at least one"), ({"add": ["Nope"]}, "Unknown tag"),
                            ({"add": ["Business"], "remove": ["business"]}, "same tag")):
                with self.assertRaises(ValueError, msg=kw) as cm:
                    self.run_(service.update_transaction_tags(self.writer(), "301", apply=True, **kw))
                self.assertIn(msg, str(cm.exception))
        self.assertEqual(m.mutations(), [])

    def test_ambiguous_name_needs_id(self):
        items = [{"id": "g1", "name": "Work"}, {"id": "g9", "name": "work"}]
        with self.assertRaises(ValueError) as cm:
            service._pick("tag", items, "WORK", "")
        self.assertIn("ambiguous", str(cm.exception))
        self.assertEqual(service._pick("tag", items, "g9", "")["name"], "work")
