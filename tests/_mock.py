"""Local mock of Monarch's API for tests. Never talks to the real service."""
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from monarch_money_cli import client

TOKEN = "a" * 64

ACCOUNTS = {"accounts": [
    {"id": "1", "displayName": "Checking", "isHidden": False, "isAsset": True, "currentBalance": 1200.5,
     "includeInNetWorth": True, "type": {"name": "depository"}, "subtype": {"name": "checking"},
     "mask": "9999"},
    {"id": "2", "displayName": "Old card", "isHidden": True, "isAsset": False, "currentBalance": 0,
     "includeInNetWorth": False, "type": {"name": "credit"}, "subtype": None},
]}
TXNS = {"allTransactions": {"totalCount": 3, "results": [
    {"id": "t1", "amount": -42.1, "pending": False, "date": "2026-09-02", "notes": "secret note",
     "category": {"name": "Groceries"}, "merchant": {"name": "King Soopers"}, "account": {"displayName": "Checking"},
     "businessEntity": {"name": "Acme LLC"}, "tags": [{"name": "business"}, {"name": "supplies"}]},
    {"id": "t2", "amount": 2500, "pending": True, "date": "2026-09-01", "notes": None,
     "category": {"name": "Paycheck"}, "merchant": {"name": "Employer"}, "account": {"displayName": "Checking"},
     "businessEntity": None, "tags": []},
]}}
TAGS = {"householdTransactionTags": [{"id": "g1", "name": "Business"}, {"id": "g2", "name": "Supplies"}]}
CASHFLOW = {
    "byCategory": [
        {"groupBy": {"category": {"name": "Interest", "group": {"type": "income"}}}, "summary": {"sum": 12.5}},
        {"groupBy": {"category": {"name": "Paycheck", "group": {"type": "income"}}}, "summary": {"sum": 5000}},
        {"groupBy": {"category": {"name": "Groceries", "group": {"type": "expense"}}}, "summary": {"sum": -300}},
        {"groupBy": {"category": {"name": "Rent", "group": {"type": "expense"}}}, "summary": {"sum": -1500}},
        {"groupBy": {"category": {"name": "Credit Card Payment", "group": {"type": "transfer"}}},
         "summary": {"sum": -1000}},
    ],
    "summary": [{"summary": {"sumIncome": 5000, "sumExpense": -300, "savings": 4700, "savingsRate": 0.94}}],
}

# Placeholder entities. notes/description/logoUrl are here only to prove they're never returned.
ENTITIES = {"businessEntities": [
    {"id": "101", "name": "Acme LLC", "structure": "llc", "accountsCount": 1, "transactionsCount": 40,
     "notes": "secret entity note", "description": "private", "logoUrl": "https://example.invalid/logo.png",
     "accounts": [{"id": "1", "displayName": "Checking"}]},
    {"id": "102", "name": "Acme Labs", "structure": "s_corp", "accountsCount": 0, "transactionsCount": 0,
     "accounts": []},
]}
ENTITY_SUMMARIES = {"businessEntitySummaries": [
    {"businessEntity": None,
     "summary": {"sumIncome": 1000, "sumExpense": -250, "savings": 750, "savingsRate": 0.75, "count": 7}},
    {"businessEntity": {"id": "101", "name": "Acme LLC"},
     "summary": {"sumIncome": 4000, "sumExpense": -50, "savings": 3950, "savingsRate": 0.9875, "count": 3}},
]}

# Placeholder planning data. imageStorageProviderId and archived goals prove trimming and filtering.
PLANNING = {
    "categoryGroups": [
        {"id": "cg1", "name": "Income", "type": "income", "categories": [{"id": "c1", "name": "Paycheck"}]},
        {"id": "cg2", "name": "Living", "type": "expense",
         "categories": [{"id": "c2", "name": "Groceries"}, {"id": "c3", "name": "Rent"}, {"id": "c4", "name": "Gifts"}]},
        {"id": "cg3", "name": "Transfers", "type": "transfer",
         "categories": [{"id": "c5", "name": "Credit Card Payment"}]},
    ],
    "budgetData": {
        "monthlyAmountsByCategory": [
            {"category": {"id": "c1"}, "monthlyAmounts": [
                {"month": "2026-09-01", "plannedCashFlowAmount": 5000, "actualAmount": 5000, "remainingAmount": 0},
                {"month": "2026-10-01", "plannedCashFlowAmount": 5000, "actualAmount": 0, "remainingAmount": 5000}]},
            {"category": {"id": "c2"}, "monthlyAmounts": [
                {"month": "2026-09-01", "plannedCashFlowAmount": 400, "actualAmount": 300, "remainingAmount": 100}]},
            {"category": {"id": "c3"}, "monthlyAmounts": [
                {"month": "2026-09-01", "plannedCashFlowAmount": 1500, "actualAmount": 1500, "remainingAmount": 0}]},
            {"category": {"id": "c4"}, "monthlyAmounts": [
                {"month": "2026-09-01", "plannedCashFlowAmount": 0, "actualAmount": 0, "remainingAmount": 0}]},
            {"category": {"id": "c5"}, "monthlyAmounts": [
                {"month": "2026-09-01", "plannedCashFlowAmount": 0, "actualAmount": 1000, "remainingAmount": 0}]},
        ],
        "totalsByMonth": [
            {"month": "2026-09-01",
             "totalIncome": {"plannedAmount": 5000, "actualAmount": 5000, "remainingAmount": 0},
             "totalExpenses": {"plannedAmount": 1900, "actualAmount": 1800, "remainingAmount": 100}},
            {"month": "2026-10-01",
             "totalIncome": {"plannedAmount": 5000, "actualAmount": 0, "remainingAmount": 5000},
             "totalExpenses": {"plannedAmount": 1900, "actualAmount": 0, "remainingAmount": 1900}},
        ],
    },
    "goalsV2": [
        {"id": "gl1", "name": "Emergency fund", "archivedAt": None, "completedAt": None, "priority": 1,
         "imageStorageProviderId": "secret-image-id",
         "plannedContributions": [{"month": "2026-09-01", "amount": 500}, {"month": "2026-10-01", "amount": 500}],
         "monthlyContributionSummaries": [{"month": "2026-09-01", "sum": 450}]},
        {"id": "gl2", "name": "Old car", "archivedAt": "2025-01-01T00:00:00Z", "completedAt": None, "priority": 2,
         "plannedContributions": [], "monthlyContributionSummaries": []},
    ],
}
RECURRING = {"recurringTransactionItems": [
    {"stream": {"id": "s2", "frequency": "monthly", "amount": -1500, "isApproximate": False,
                "merchant": {"name": "Landlord", "logoUrl": "https://example.invalid/l.png"}},
     "date": "2026-09-15", "isPast": False, "transactionId": None, "amount": -1500,
     "category": {"name": "Rent"}, "account": {"id": "1", "displayName": "Checking"}},
    {"stream": {"id": "s1", "frequency": "monthly", "amount": -15.49, "isApproximate": True,
                "merchant": {"name": "Netflix"}},
     "date": "2026-09-03", "isPast": True, "transactionId": "t9", "amount": -15.49,
     "category": {"name": "Streaming"}, "account": {"id": "1", "displayName": "Checking"}},
    {"stream": {"id": "s3", "frequency": "monthly", "amount": -60, "isApproximate": False,
                "merchant": {"name": "=cmd()"}},
     "date": "2026-09-05", "isPast": True, "transactionId": None, "amount": -60,
     "category": None, "account": None},
]}
HOLDINGS = {"portfolio": {"aggregateHoldings": {"edges": [
    {"node": {"id": "h1", "quantity": 10, "basis": 1000, "totalValue": 1500,
              "holdings": [{"name": "Vanguard Total", "ticker": "VTI", "typeDisplay": "ETF"}],
              "security": {"name": "Vanguard Total Stock Market ETF", "ticker": "VTI", "typeDisplay": "ETF",
                           "currentPrice": 150}}},
    {"node": {"id": "h2", "quantity": 1, "basis": None, "totalValue": 5000,
              "holdings": [{"name": "Private fund", "ticker": None, "typeDisplay": "Other"}], "security": None}},
]}}}
NET_WORTH = {"aggregateSnapshots": [
    {"date": "2026-07-01", "balance": 100000}, {"date": "2026-07-31", "balance": 101000},
    {"date": "2026-08-15", "balance": 99000}, {"date": "2026-08-31", "balance": 103000},
    {"date": "2026-09-10", "balance": 104500.5},
]}


class MockMonarch:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.force_status: int | None = None  # set to 502/503/504/429 to simulate an upstream outage
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                if outer.force_status is not None:
                    self.send_response(outer.force_status)
                    self.end_headers()
                    return
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:9/steal")
                    self.end_headers()
                    return
                if self.path == "/huge":
                    self._json({"x": "y" * (client.MAX_RESPONSE_BYTES + 10)})
                    return
                if self.headers.get("Authorization") != f"Token {TOKEN}":
                    self.send_response(401)
                    self.end_headers()
                    return
                op = body.get("operationName")
                data = {"GetAccounts": ACCOUNTS, "GetTransactionsList": TXNS, "GetHouseholdTransactionTags": TAGS, "Web_GetCashFlowPage": CASHFLOW,
                        "Common_GetBusinessEntities": ENTITIES,
                        "Web_GetBusinessEntitySummaries": ENTITY_SUMMARIES, "GetJointPlanningData": PLANNING,
                        "Web_GetUpcomingRecurringTransactionItems": RECURRING, "Web_GetHoldings": HOLDINGS,
                        "GetAggregateSnapshots": NET_WORTH}[op]
                self._json({"data": data})

            def _json(self, obj):
                raw = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._saved = (client.BASE_URL, client._OPENER)
        client.BASE_URL = f"http://127.0.0.1:{self.server.server_address[1]}"
        # Same hardening as production, plus plain HTTP for the local mock only.
        client._OPENER = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPHandler(), client._NoRedirect())
        return self

    def __exit__(self, *exc):
        client.BASE_URL, client._OPENER = self._saved
        self.server.shutdown()
        self.server.server_close()
