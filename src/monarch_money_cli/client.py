"""Minimal Monarch Money client. Standard library only.

Talks to Monarch's private (reverse-engineered) API: POST /auth/login/ for a
session token, POST /graphql for reads. The only writes are the mutations in
ALLOWED_MUTATIONS, sent only by a client built with allow_writes=True.
"""
import asyncio
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from monarch_money_cli import __version__

BASE_URL = "https://api.monarch.com"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_SSL = ssl.create_default_context()  # system CAs, hostname checking on


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib would re-send the Authorization header to a redirect target. Refuse all redirects.
    def redirect_request(self, *args, **kwargs):  # type: ignore[override]
        return None


# Empty ProxyHandler: ignore HTTP(S)_PROXY env vars so the token can't be routed through a proxy.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=_SSL),
    _NoRedirect(),
)
_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Client-Platform": "web",
    "User-Agent": f"monarch-money-mcp-cli/{__version__}",
}


class MonarchError(RuntimeError):
    pass


class AuthRequired(MonarchError):
    pass


class MonarchUnavailable(MonarchError):
    """Monarch is down, overloaded, or unreachable. Transient; no retry is attempted here."""


class MFARequired(MonarchError):
    pass


class CaptchaRequired(MonarchError):
    pass


def _post(path: str, body: dict[str, Any], token: str | None, timeout: int) -> tuple[int, dict[str, Any]]:
    if not path.startswith("/") or "//" in path or "@" in path:
        raise MonarchError("Invalid API path.")  # paths are internal constants; belt and braces
    headers = dict(_HEADERS)
    if token:
        headers["Authorization"] = f"Token {token}"
    req = urllib.request.Request(  # noqa: S310 - https constant host
        BASE_URL + path, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        # Scheme and host come only from the BASE_URL constant (https).
        with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise MonarchError("Response too large; narrow the date range.")
            return resp.status, json.loads(raw or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read(64 * 1024) or b"{}")
        except ValueError:
            payload = {}
        if e.code in (429, 502, 503, 504):
            raise MonarchUnavailable(
                f"Monarch is temporarily unavailable (HTTP {e.code}). Try again in a minute."
            ) from None
        return e.code, payload
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            # A cert failure isn't transient and isn't safe to imply "just retry": it usually means
            # something on this network (proxy, VPN, captive portal) is intercepting the connection.
            raise MonarchError(
                "Could not verify api.monarch.com's TLS certificate, so nothing was sent. "
                "Check for a proxy, VPN, or captive portal on this network."
            ) from None
        # Covers connection refused, DNS failure, and socket timeouts (all reach here as URLError).
        raise MonarchUnavailable(
            f"Monarch is temporarily unavailable ({type(e.reason).__name__}). Try again in a minute."
        ) from None
    except (TimeoutError, ConnectionError) as e:
        # A timeout or dropped connection while reading the response body isn't wrapped in URLError.
        raise MonarchUnavailable(
            f"Monarch is temporarily unavailable ({type(e).__name__}). Try again in a minute."
        ) from None


# ---------- login (used only by the auth CLI) ----------

def login(email: str, password: str, mfa_code: str | None = None, timeout: int = 20) -> str:
    """Return a long-lived session token. Raises MFARequired if a code is needed."""
    body = {"username": email, "password": password, "supports_mfa": True, "trusted_device": True}
    if mfa_code:
        body["totp"] = mfa_code
    status, data = _post("/auth/login/", body, None, timeout)

    if status == 403:
        if data.get("error_code") == "CAPTCHA_REQUIRED":
            raise CaptchaRequired("Login blocked by CAPTCHA. Use `monarch auth paste-token` instead.")
        raise MFARequired("MFA code required.")
    if status != 200:
        raise MonarchError(f"Login failed: {data.get('detail') or data.get('error_code') or f'HTTP {status}'}")

    token = data.get("token")
    if not token:
        raise MonarchError("Login succeeded but no token was returned.")
    if token.count(".") == 2:
        raise MonarchError("Got a short-lived JWT instead of a session token. Refusing to store it.")
    if data.get("tokenExpiration") not in (None, "null"):
        raise MonarchError("Got a short-lived token. Retry the login with MFA.")
    return token


# ---------- GraphQL reads ----------

Q_ACCOUNTS = """
query GetAccounts($filters: AccountFilters) {
  accounts(filters: $filters) {
    id displayName isHidden isAsset currentBalance includeInNetWorth
    type { name } subtype { name }
  }
}"""

Q_TRANSACTIONS = """
query GetTransactionsList($offset: Int, $limit: Int, $filters: TransactionFilterInput, $orderBy: TransactionOrdering) {
  allTransactions(filters: $filters) {
    totalCount
    results(offset: $offset, limit: $limit, orderBy: $orderBy) {
      id amount pending date
      category { name }
      merchant { name }
      account { displayName }
      businessEntity { name }
      tags { name }
    }
  }
}"""

Q_CASHFLOW = """
query Web_GetCashFlowPage($filters: TransactionFilterInput) {
  byCategory: aggregates(filters: $filters, groupBy: ["category"]) {
    groupBy { category { name group { type } } }
    summary { sum }
  }
  summary: aggregates(filters: $filters, fillEmptyValues: true) {
    summary { sumIncome sumExpense savings savingsRate }
  }
}"""

Q_TAGS = """
query GetHouseholdTransactionTags {
  householdTransactionTags { id name }
}"""

# Never select notes, description, or logoUrl: free text and URLs the CLI has no use for.
Q_ENTITIES = """
query Common_GetBusinessEntities {
  businessEntities {
    id name structure accountsCount transactionsCount
    accounts { id displayName }
  }
}"""

Q_ENTITY_SUMMARIES = """
query Web_GetBusinessEntitySummaries($filters: TransactionFilterInput!) {
  businessEntitySummaries(filters: $filters) {
    businessEntity { id name }
    summary { sumIncome sumExpense savings savingsRate count }
  }
}"""


# Budgets and goals come from one planning query. Goal image fields are never selected.
Q_PLANNING = """
query GetJointPlanningData($startDate: Date!, $endDate: Date!) {
  budgetData(startMonth: $startDate, endMonth: $endDate) {
    monthlyAmountsByCategory {
      category { id }
      monthlyAmounts { month plannedCashFlowAmount actualAmount remainingAmount }
    }
    totalsByMonth {
      month
      totalIncome { plannedAmount actualAmount remainingAmount }
      totalExpenses { plannedAmount actualAmount remainingAmount }
    }
  }
  categoryGroups { id name type categories { id name } }
  goalsV2 {
    id name archivedAt completedAt priority
    plannedContributions(startMonth: $startDate, endMonth: $endDate) { month amount }
    monthlyContributionSummaries(startMonth: $startDate, endMonth: $endDate) { month sum }
  }
}"""

# Never select logoUrl.
Q_RECURRING = """
query Web_GetUpcomingRecurringTransactionItems($startDate: Date!, $endDate: Date!, $filters: RecurringTransactionFilter) {
  recurringTransactionItems(startDate: $startDate, endDate: $endDate, filters: $filters) {
    stream { id frequency amount isApproximate merchant { name } }
    date isPast transactionId amount
    category { name }
    account { id displayName }
  }
}"""

Q_HOLDINGS = """
query Web_GetHoldings($input: PortfolioInput) {
  portfolio(input: $input) {
    aggregateHoldings {
      edges {
        node {
          id quantity basis totalValue
          holdings { name ticker typeDisplay }
          security { name ticker typeDisplay currentPrice }
        }
      }
    }
  }
}"""

Q_NET_WORTH = """
query GetAggregateSnapshots($filters: AggregateSnapshotFilters) {
  aggregateSnapshots(filters: $filters) { date balance }
}"""

Q_CATEGORIES = """
query GetCategories {
  categories { id name isDisabled group { name type } }
}"""

# One transaction's current state, read before a write. Never select notes.
Q_TRANSACTION = """
query GetTransactionDrawer($id: UUID!) {
  getTransaction(id: $id) {
    id date amount
    merchant { name }
    category { id name }
    tags { id name }
  }
}"""


# ---------- GraphQL writes ----------

@dataclass(frozen=True)
class Mutation:
    does: str  # one sentence: what it changes
    why: str  # one sentence: why the package is allowed to send it
    input_keys: frozenset[str]  # exact keys of variables["input"]


# The only mutations this package may send. Anything not listed is refused before any HTTP call.
# Adding an entry requires a `does`, a `why`, a SECURITY.md row, and tests.
ALLOWED_MUTATIONS: dict[str, Mutation] = {
    "Web_TransactionDrawerUpdateTransaction": Mutation(
        does="Set one transaction's category to an existing category.",
        why="Fix miscategorised transactions.",
        input_keys=frozenset({"id", "category"})),
    "Web_SetTransactionTags": Mutation(
        does="Replace one transaction's tags with existing tags.",
        why="Tag transactions for reporting.",
        input_keys=frozenset({"transactionId", "tagIds"})),
}

M_SET_CATEGORY = """
mutation Web_TransactionDrawerUpdateTransaction($input: UpdateTransactionMutationInput!) {
  updateTransaction(input: $input) {
    transaction { id category { id name } }
    errors { message code fieldErrors { field messages } }
  }
}"""

# Replaces the transaction's full tag list.
M_SET_TAGS = """
mutation Web_SetTransactionTags($input: SetTransactionTagsInput!) {
  setTransactionTags(input: $input) {
    transaction { id tags { id name } }
    errors { message code fieldErrors { field messages } }
  }
}"""

_MUTATION_WORD = re.compile(r"\bmutation\b")
_OPERATION_WORD = re.compile(r"\b(?:query|mutation|subscription)\b")


def _check_mutation(op: str, query: str, variables: dict[str, Any]) -> None:
    """Raise MonarchError unless this exact mutation is allowed. Runs before any HTTP call."""
    allowed = ALLOWED_MUTATIONS.get(op)
    if allowed is None:
        raise MonarchError(f"Refusing {op!r}: it isn't in the mutation allowlist.")
    if (len(_OPERATION_WORD.findall(query)) != 1
            or not re.match(rf"\s*mutation\s+{re.escape(op)}\s*[({{]", query)):
        raise MonarchError(f"Refusing {op!r}: the query text isn't that single mutation.")
    inp = variables.get("input")
    if set(variables) != {"input"} or not isinstance(inp, dict) or set(inp) != allowed.input_keys:
        raise MonarchError(f"Refusing {op!r}: its input must have exactly {sorted(allowed.input_keys)}.")


def _payload_errors(data: dict[str, Any]) -> str | None:
    """Messages from a mutation payload's `errors` field, or None when it reports none."""
    msgs = []
    for payload in data.values():
        errs = payload.get("errors") if isinstance(payload, dict) else None
        for e in errs if isinstance(errs, list) else [errs] if errs else []:
            if not isinstance(e, dict):
                msgs.append(str(e))
                continue
            fields = [f"{f.get('field')}: {', '.join(map(str, f.get('messages') or []))}"
                      for f in e.get("fieldErrors") or [] if isinstance(f, dict)]
            parts = [str(p) for p in (e.get("message"), e.get("code"), *fields) if p]
            if parts:  # an errors object with every field empty means no error
                msgs.append("; ".join(parts))
    return "; ".join(msgs[:3]) or None


def _filters(start_date: str, end_date: str, search: str = "", accounts: list[str] | None = None,
             entity_set: dict[str, Any] | None = None) -> dict[str, Any]:
    f = {"search": search, "categories": [], "accounts": accounts or [], "tags": [],
         "startDate": start_date, "endDate": end_date}
    if entity_set is not None:
        f["businessEntitySet"] = entity_set  # {"businessEntityIds": [...], "includeUnassigned": bool}
    return f


class MonarchClient:
    def __init__(self, token: str, timeout: int = 20, allow_writes: bool = False) -> None:
        self._token = token
        self._timeout = timeout
        self._allow_writes = allow_writes

    async def _gql(self, operation: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Reads only: refuses any query text containing a mutation."""
        if _MUTATION_WORD.search(query):
            raise MonarchError(f"Refusing {operation!r}: mutations go through _mutate, not _gql.")
        return await self._send(operation, query, variables or {})

    async def _mutate(self, operation: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Send one allowlisted mutation. Refused unless the client was built with allow_writes=True."""
        if not self._allow_writes:
            raise MonarchError("Writes are off: this client was not built with allow_writes=True.")
        _check_mutation(operation, query, variables)
        data = await self._send(operation, query, variables)
        err = _payload_errors(data)
        if err:
            raise MonarchError(f"Monarch refused the change: {err}")
        return data

    async def _send(self, operation: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = {"operationName": operation, "query": query, "variables": variables}
        status, data = await asyncio.to_thread(_post, "/graphql", body, self._token, self._timeout)
        if status in (401, 403):
            raise AuthRequired("Monarch session expired or revoked.")
        if status != 200:
            raise MonarchError(f"Monarch returned HTTP {status}.")
        if data.get("errors"):
            # Messages only; never echo request details.
            msgs = "; ".join(str(e.get("message", "unknown")) for e in data["errors"][:3])
            raise MonarchError(f"GraphQL error: {msgs}")
        return data.get("data") or {}

    async def accounts(self, entity_set: dict[str, Any] | None = None) -> dict[str, Any]:
        if entity_set is None:
            return await self._gql("GetAccounts", Q_ACCOUNTS)
        # An entity filter drops hidden accounts unless includeHidden is set; callers filter them.
        return await self._gql("GetAccounts", Q_ACCOUNTS, {
            "filters": {"businessEntitySet": entity_set, "includeHidden": True}})

    async def transactions(self, start_date: str, end_date: str, search: str, account_ids: list[str],
                           limit: int, offset: int, entity_set: dict[str, Any] | None = None,
                           tag_ids: list[str] | None = None) -> dict[str, Any]:
        filters = _filters(start_date, end_date, search, account_ids, entity_set)
        filters["tags"] = tag_ids or []
        return await self._gql("GetTransactionsList", Q_TRANSACTIONS, {
            "offset": offset, "limit": limit, "orderBy": "date", "filters": filters,
        })

    async def tags(self) -> dict[str, Any]:
        return await self._gql("GetHouseholdTransactionTags", Q_TAGS)

    async def cashflow(self, start_date: str, end_date: str,
                       entity_set: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._gql("Web_GetCashFlowPage", Q_CASHFLOW,
                               {"filters": _filters(start_date, end_date, entity_set=entity_set)})

    async def entities(self) -> dict[str, Any]:
        return await self._gql("Common_GetBusinessEntities", Q_ENTITIES)

    async def entity_summaries(self, start_date: str, end_date: str) -> dict[str, Any]:
        return await self._gql("Web_GetBusinessEntitySummaries", Q_ENTITY_SUMMARIES,
                               {"filters": _filters(start_date, end_date)})

    async def planning(self, start_date: str, end_date: str) -> dict[str, Any]:
        return await self._gql("GetJointPlanningData", Q_PLANNING, {"startDate": start_date, "endDate": end_date})

    async def recurring(self, start_date: str, end_date: str) -> dict[str, Any]:
        return await self._gql("Web_GetUpcomingRecurringTransactionItems", Q_RECURRING,
                               {"startDate": start_date, "endDate": end_date})

    async def holdings(self, account_ids: list[str], as_of: str) -> dict[str, Any]:
        return await self._gql("Web_GetHoldings", Q_HOLDINGS, {"input": {
            "accountIds": account_ids, "startDate": as_of, "endDate": as_of, "includeHiddenHoldings": True}})

    async def net_worth(self, start_date: str, end_date: str) -> dict[str, Any]:
        return await self._gql("GetAggregateSnapshots", Q_NET_WORTH,
                               {"filters": {"startDate": start_date, "endDate": end_date}})

    async def categories(self) -> dict[str, Any]:
        return await self._gql("GetCategories", Q_CATEGORIES)

    async def transaction(self, txn_id: str) -> dict[str, Any]:
        return await self._gql("GetTransactionDrawer", Q_TRANSACTION, {"id": txn_id})

    async def set_transaction_category(self, txn_id: str, category_id: str) -> dict[str, Any]:
        return await self._mutate("Web_TransactionDrawerUpdateTransaction", M_SET_CATEGORY,
                                  {"input": {"id": txn_id, "category": category_id}})

    async def set_transaction_tags(self, txn_id: str, tag_ids: list[str]) -> dict[str, Any]:
        return await self._mutate("Web_SetTransactionTags", M_SET_TAGS,
                                  {"input": {"transactionId": txn_id, "tagIds": list(tag_ids)}})
