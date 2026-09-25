"""Shared read operations used by both the CLI and the MCP server.

Every function returns trimmed, plain dicts: only the fields a user or model
needs. No account masks, institution IDs, or transaction notes.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from monarch_money_cli import keychain
from monarch_money_cli.client import AuthRequired, MonarchClient

MAX_PAGE = 100
HOUSEHOLD = "household"  # --entity keyword for transactions and accounts with no business entity


def client() -> MonarchClient:
    token = keychain.load()
    if not token:
        raise AuthRequired("Not logged in. Run: monarch auth login")
    return MonarchClient(token)


def date_range(start: str | None, end: str | None, today: date | None = None) -> tuple[str, str]:
    """Validate a YYYY-MM-DD range. Both omitted means the current month."""
    if start is None and end is None:
        t = today or date.today()
        return (t.replace(day=1).isoformat(),
                t.replace(day=calendar.monthrange(t.year, t.month)[1]).isoformat())
    if start is None or end is None:
        raise ValueError("Give both a start and an end date, or neither for the current month.")
    try:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise ValueError("Dates must be YYYY-MM-DD.") from None
    if s > e:
        raise ValueError("Start date must be on or before end date.")
    return s.isoformat(), e.isoformat()


MAX_DAYS = 36600  # --days upper bound (about 100 years)


class DateUsageError(ValueError):
    """Conflicting or incomplete date options (a usage error, not a bad value)."""


def _month(y: int, m: int) -> tuple[str, str]:
    return date(y, m, 1).isoformat(), date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def period_range(start: str | None = None, end: str | None = None, *, month: str | None = None,
                 year: str | None = None, ytd: bool = False, last_month: bool = False,
                 days: int | None = None, from_: str | None = None, to: str | None = None,
                 today: date | None = None) -> tuple[str, str]:
    """Resolve one date selection to a YYYY-MM-DD range.

    Exactly one of: positional start/end, month (YYYY-MM), year (YYYY), ytd, last_month,
    days (last N days through today), or from_ with an optional to (default today).
    Nothing given means the current month.
    """
    picked = [n for n, v in [("START END", start is not None or end is not None), ("--month", month is not None),
                             ("--year", year is not None), ("--ytd", ytd), ("--last-month", last_month),
                             ("--days", days is not None), ("--from/--to", from_ is not None or to is not None)]
              if v]
    if len(picked) > 1:
        raise DateUsageError(f"Pick one date option, not {' and '.join(picked)}.")
    t = today or date.today()
    if month is not None:
        try:
            y, m = month.split("-")
            if not (len(y) == 4 and len(m) == 2 and y.isdigit() and m.isdigit()):
                raise ValueError
            return _month(int(y), int(m))
        except ValueError:
            raise ValueError("--month must be YYYY-MM.") from None
    if year is not None:
        if len(year) != 4 or not year.isdigit() or year == "0000":
            raise ValueError("--year must be YYYY.")
        return f"{year}-01-01", f"{year}-12-31"
    if ytd:
        return date(t.year, 1, 1).isoformat(), t.isoformat()
    if last_month:
        prev = t.replace(day=1) - timedelta(days=1)
        return _month(prev.year, prev.month)
    if days is not None:
        if not 1 <= days <= MAX_DAYS:
            raise ValueError(f"--days must be between 1 and {MAX_DAYS}.")
        return (t - timedelta(days=days - 1)).isoformat(), t.isoformat()
    if from_ is not None or to is not None:
        if from_ is None:
            raise DateUsageError("--to needs --from (use --from alone to run through today).")
        return date_range(from_, to or t.isoformat(), today=t)
    return date_range(start, end, today=t)


async def entities(c: MonarchClient) -> dict[str, Any]:
    """Business entities. Names are user-entered: treat them as untrusted text."""
    data = await c.entities()
    out = [{
        "id": e.get("id"),
        "name": e.get("name"),
        "structure": e.get("structure"),
        "accounts_count": e.get("accountsCount"),
        "transactions_count": e.get("transactionsCount"),
        "accounts": [{"id": a.get("id"), "name": a.get("displayName")} for a in e.get("accounts") or []],
    } for e in data.get("businessEntities") or []]
    return {"count": len(out), "entities": out}


def _valid_entities(ents: list[dict[str, Any]]) -> str:
    names = [f"{e['name']!r} (id {e['id']})" for e in ents]
    return "Valid: " + ", ".join([*names, HOUSEHOLD]) + ". See `monarch entities`."


async def resolve_tags(c: MonarchClient, names: list[str] | None) -> list[dict[str, str]]:
    """Tag names (case-insensitive, exact) to [{"id", "name"}]. Tag names are untrusted text."""
    if not names:
        return []
    tags = [{"id": t.get("id"), "name": t.get("name") or ""}
            for t in (await c.tags()).get("householdTransactionTags") or []]
    picked = []
    for raw in names:
        match = [t for t in tags if t["name"].casefold() == raw.strip().casefold()]
        if not match:
            valid = ", ".join(sorted(repr(t["name"]) for t in tags))
            raise ValueError(f"Unknown tag {raw!r}. Valid: {valid}.")
        picked.append(match[0])
    return picked


async def resolve_entities(c: MonarchClient, specs: list[str] | None) -> dict[str, Any] | None:
    """Turn --entity values (id, name, or `household`) into a scope, or None for no scope.

    Names match case-insensitively, exactly or by unique prefix. The scope is
    {"entity_ids", "entity_names", "include_household"}; `entity_set()` turns it
    into Monarch's BusinessEntitySetInput.
    """
    if not specs:
        return None
    ents = (await entities(c))["entities"]
    picked: dict[str, str] = {}
    household = False
    for raw in specs:
        spec = raw.strip()
        if spec.lower() == HOUSEHOLD:
            household = True
            continue
        if spec.isdigit():
            match = [e for e in ents if e["id"] == spec]
        else:
            key = spec.casefold()
            match = ([e for e in ents if (e["name"] or "").casefold() == key]
                     or [e for e in ents if key and (e["name"] or "").casefold().startswith(key)])
        if not match:
            raise ValueError(f"Unknown entity {spec!r}. {_valid_entities(ents)}")
        if len(match) > 1:
            raise ValueError(f"Entity {spec!r} is ambiguous; use its id. {_valid_entities(match)}")
        picked[match[0]["id"]] = match[0]["name"]
    return {"entity_ids": list(picked), "entity_names": list(picked.values()), "include_household": household}


def entity_set(scope: dict[str, Any] | None) -> dict[str, Any] | None:
    """Monarch's BusinessEntitySetInput. includeUnassigned is required (non-null) by the API."""
    if scope is None:
        return None
    return {"businessEntityIds": list(scope["entity_ids"]), "includeUnassigned": bool(scope["include_household"])}


def scope_label(scope: dict[str, Any] | None) -> str:
    if scope is None:
        return "all"
    return ", ".join([*scope["entity_names"], *([HOUSEHOLD] if scope["include_household"] else [])])


def _scoped(out: dict[str, Any], scope: dict[str, Any] | None) -> dict[str, Any]:
    if scope is not None:
        out["entity_scope"] = scope
    return out


async def accounts(c: MonarchClient, include_hidden: bool = False,
                   scope: dict[str, Any] | None = None) -> dict[str, Any]:
    data = await c.accounts(entity_set(scope))
    out = []
    for a in data.get("accounts") or []:
        if a.get("isHidden") and not include_hidden:
            continue
        out.append({
            "id": a.get("id"),
            "name": a.get("displayName"),
            "type": (a.get("type") or {}).get("name"),
            "subtype": (a.get("subtype") or {}).get("name"),
            "balance": a.get("currentBalance"),
            "is_asset": a.get("isAsset"),
            "in_net_worth": a.get("includeInNetWorth"),
        })
    return _scoped({"count": len(out), "accounts": out}, scope)


async def transactions(c: MonarchClient, start: str, end: str, search: str = "",
                       account_ids: list[str] | None = None, limit: int = 50,
                       offset: int = 0, scope: dict[str, Any] | None = None,
                       tags: list[dict[str, str]] | None = None) -> dict[str, Any]:
    if not 1 <= limit <= MAX_PAGE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE}.")
    if offset < 0:
        raise ValueError("offset must be >= 0.")
    data = await c.transactions(start, end, search, account_ids or [], limit, offset, entity_set(scope),
                                [t["id"] for t in tags or []])
    block = data.get("allTransactions") or {}
    total = block.get("totalCount") or 0
    txns = [{
        "id": t.get("id"),
        "date": t.get("date"),
        "amount": t.get("amount"),
        "merchant": (t.get("merchant") or {}).get("name"),
        "category": (t.get("category") or {}).get("name"),
        "account": (t.get("account") or {}).get("displayName"),
        "pending": t.get("pending"),
        "entity": (t.get("businessEntity") or {}).get("name") or "",  # "" = household
        "tags": [g.get("name") for g in t.get("tags") or [] if g.get("name")],
    } for t in block.get("results") or []]
    out = {"total": total, "offset": offset, "returned": len(txns),
           "has_more": offset + len(txns) < total, "transactions": txns}
    if tags:
        out["tag_filter"] = [t["name"] for t in tags]
    return _scoped(out, scope)


def _categories(data: dict[str, Any], group_type: str) -> list[dict[str, Any]]:
    cats = []
    for row in data.get("byCategory") or []:
        cat = (row.get("groupBy") or {}).get("category") or {}
        gtype = (cat.get("group") or {}).get("type")
        if gtype != group_type:
            continue  # transfers and other groups are neither income nor spending
        cats.append({"category": cat.get("name"), "group_type": gtype,
                     "total": (row.get("summary") or {}).get("sum")})
    # Expenses are negative: ascending puts the largest outflow first. Income: largest first.
    cats.sort(key=lambda x: x["total"] or 0, reverse=group_type == "income")
    return cats


def category_total(cats: list[dict[str, Any]]) -> float:
    return round(sum(c["total"] or 0 for c in cats), 2)


async def cashflow_summary(c: MonarchClient, start: str, end: str,
                           scope: dict[str, Any] | None = None) -> dict[str, Any]:
    data = await c.cashflow(start, end, entity_set(scope))
    rows = data.get("summary") or [{}]
    s = (rows[0] or {}).get("summary") or {}
    return _scoped({"start_date": start, "end_date": end, "income": s.get("sumIncome"),
                    "expenses": s.get("sumExpense"), "savings": s.get("savings"),
                    "savings_rate": s.get("savingsRate"),
                    "income_categories": _categories(data, "income"),
                    "expense_categories": _categories(data, "expense")}, scope)


async def spending_by_category(c: MonarchClient, start: str, end: str,
                               scope: dict[str, Any] | None = None) -> dict[str, Any]:
    cats = _categories(await c.cashflow(start, end, entity_set(scope)), "expense")
    return _scoped({"start_date": start, "end_date": end, "total": category_total(cats), "categories": cats},
                   scope)


async def income_by_category(c: MonarchClient, start: str, end: str,
                             scope: dict[str, Any] | None = None) -> dict[str, Any]:
    cats = _categories(await c.cashflow(start, end, entity_set(scope)), "income")
    return _scoped({"start_date": start, "end_date": end, "total": category_total(cats), "categories": cats},
                   scope)


async def cashflow_by_entity(c: MonarchClient, start: str, end: str) -> dict[str, Any]:
    """Income, expenses, and savings per business entity plus household, from one API call."""
    rows = []
    for r in (await c.entity_summaries(start, end)).get("businessEntitySummaries") or []:
        e = r.get("businessEntity")
        s = r.get("summary") or {}
        rows.append({"entity_id": e.get("id") if e else None,
                     "entity": e.get("name") if e else HOUSEHOLD,
                     "income": s.get("sumIncome"), "expenses": s.get("sumExpense"),
                     "savings": s.get("savings"), "savings_rate": s.get("savingsRate"),
                     "transactions": s.get("count")})
    # Named entities alphabetically, household last.
    rows.sort(key=lambda r: (r["entity_id"] is None, (r["entity"] or "").casefold()))
    income = round(sum(r["income"] or 0 for r in rows), 2)
    expenses = round(sum(r["expenses"] or 0 for r in rows), 2)
    savings = round(income + expenses, 2)
    # Monarch reports a savings rate of 0 (not negative) when savings are negative; match it.
    total = {"income": income, "expenses": expenses, "savings": savings,
             "savings_rate": max(0.0, savings / income) if income else None,
             "transactions": sum(r["transactions"] or 0 for r in rows)}
    return {"start_date": start, "end_date": end, "entities": rows, "total": total}
