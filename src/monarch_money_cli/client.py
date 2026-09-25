"""Minimal Monarch Money client. Standard library only.

Talks to Monarch's private (reverse-engineered) API: POST /auth/login/ for a
session token, POST /graphql for reads. Contains only the read queries
this server needs. No write mutations exist in this file.
"""
import asyncio
import json
import ssl
import urllib.error
import urllib.request
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


def _filters(start_date: str, end_date: str, search: str = "", accounts: list[str] | None = None,
             entity_set: dict[str, Any] | None = None) -> dict[str, Any]:
    f = {"search": search, "categories": [], "accounts": accounts or [], "tags": [],
         "startDate": start_date, "endDate": end_date}
    if entity_set is not None:
        f["businessEntitySet"] = entity_set  # {"businessEntityIds": [...], "includeUnassigned": bool}
    return f


class MonarchClient:
    def __init__(self, token: str, timeout: int = 20) -> None:
        self._token = token
        self._timeout = timeout

    async def _gql(self, operation: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {"operationName": operation, "query": query, "variables": variables or {}}
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
