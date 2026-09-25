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
                        "Web_GetBusinessEntitySummaries": ENTITY_SUMMARIES}[op]
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
