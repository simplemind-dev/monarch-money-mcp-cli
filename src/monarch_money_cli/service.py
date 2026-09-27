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
                 today: date | None = None, default: str = "month") -> tuple[str, str]:
    """Resolve one date selection to a YYYY-MM-DD range.

    Exactly one of: positional start/end, month (YYYY-MM), year (YYYY), ytd, last_month,
    days (last N days through today), or from_ with an optional to (default today).
    Nothing given means the current month, or with default="year", the 12 months through today.
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
    if default == "year" and start is None and end is None:
        return trailing_year(t)
    return date_range(start, end, today=t)


def trailing_year(today: date | None = None) -> tuple[str, str]:
    """The first of the month 11 months ago through today: 12 calendar months."""
    t = today or date.today()
    y, m = divmod(t.year * 12 + t.month - 1 - 11, 12)
    return date(y, m + 1, 1).isoformat(), t.isoformat()


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


def _in_months(month: str | None, start: str, end: str) -> bool:
    """Whether a Monarch month ("YYYY-MM-01" or "YYYY-MM") falls in the start..end range."""
    return bool(month) and start[:7] <= month[:7] <= end[:7]


def _plan(block: dict[str, Any] | None) -> dict[str, Any]:
    b = block or {}
    return {"budgeted": b.get("plannedAmount"), "actual": b.get("actualAmount"), "remaining": b.get("remainingAmount")}


async def budgets(c: MonarchClient, start: str, end: str) -> dict[str, Any]:
    """Budgeted vs actual per month: income and expense totals, and every category with a budget
    or activity. Amounts are as Monarch's budget shows them: expenses and income both positive."""
    data = await c.planning(start, end)
    cats: dict[str, dict[str, Any]] = {}
    for g in data.get("categoryGroups") or []:
        for cat in g.get("categories") or []:
            cats[cat.get("id")] = {"category": cat.get("name"), "group": g.get("name"), "group_type": g.get("type")}
    budget = data.get("budgetData") or {}
    months: dict[str, dict[str, Any]] = {}
    for t in budget.get("totalsByMonth") or []:
        if _in_months(t.get("month"), start, end):
            months[t["month"][:7]] = {"month": t["month"][:7], "income": _plan(t.get("totalIncome")),
                                      "expenses": _plan(t.get("totalExpenses")), "categories": []}
    for row in budget.get("monthlyAmountsByCategory") or []:
        info = cats.get(((row.get("category") or {}).get("id")))
        if info is None or info["group_type"] not in ("income", "expense"):
            continue  # transfers aren't budgeted
        for a in row.get("monthlyAmounts") or []:
            month = (a.get("month") or "")[:7]
            if month not in months or not (a.get("plannedCashFlowAmount") or a.get("actualAmount")):
                continue
            months[month]["categories"].append({**info, "budgeted": a.get("plannedCashFlowAmount"),
                                                "actual": a.get("actualAmount"),
                                                "remaining": a.get("remainingAmount")})
    for m in months.values():
        # Income first, then expenses; largest budget first within each.
        m["categories"].sort(key=lambda x: (x["group_type"] != "income", -(x["budgeted"] or 0),
                                            (x["category"] or "").casefold()))
    return {"start_date": start, "end_date": end, "months": sorted(months.values(), key=lambda m: m["month"])}


async def goals(c: MonarchClient, start: str, end: str, include_archived: bool = False) -> dict[str, Any]:
    """Savings goals with planned and actual contributions in the range. Names are user-entered."""
    out = []
    for g in (await c.planning(start, end)).get("goalsV2") or []:
        if g.get("archivedAt") and not include_archived:
            continue
        planned = [p for p in g.get("plannedContributions") or [] if _in_months(p.get("month"), start, end)]
        actual = [s for s in g.get("monthlyContributionSummaries") or [] if _in_months(s.get("month"), start, end)]
        out.append({
            "id": g.get("id"),
            "name": g.get("name"),
            "priority": g.get("priority"),
            "status": "archived" if g.get("archivedAt") else "completed" if g.get("completedAt") else "active",
            "planned": round(sum(p.get("amount") or 0 for p in planned), 2),
            "contributed": round(sum(s.get("sum") or 0 for s in actual), 2),
        })
    out.sort(key=lambda g: (g["priority"] is None, g["priority"] or 0, (g["name"] or "").casefold()))
    return {"start_date": start, "end_date": end, "count": len(out), "goals": out}


async def recurring(c: MonarchClient, start: str, end: str) -> dict[str, Any]:
    """Recurring bills and income due in the range, earliest first. Merchant names are untrusted."""
    items = []
    for r in (await c.recurring(start, end)).get("recurringTransactionItems") or []:
        stream = r.get("stream") or {}
        items.append({
            "date": r.get("date"),
            "merchant": (stream.get("merchant") or {}).get("name"),
            "amount": r.get("amount"),
            "frequency": stream.get("frequency"),
            "approximate": bool(stream.get("isApproximate")),
            "category": (r.get("category") or {}).get("name"),
            "account": (r.get("account") or {}).get("displayName"),
            "status": "paid" if r.get("transactionId") else "missed" if r.get("isPast") else "upcoming",
        })
    items.sort(key=lambda i: (i["date"] or "", (i["merchant"] or "").casefold()))
    return {"start_date": start, "end_date": end, "count": len(items),
            "total": round(sum(i["amount"] or 0 for i in items), 2), "items": items}


INVESTMENT_TYPE = "brokerage"


async def holdings(c: MonarchClient, account_ids: list[str] | None = None,
                   today: date | None = None) -> dict[str, Any]:
    """Investment holdings, largest value first, combined across the given accounts (default:
    every visible brokerage account)."""
    if not account_ids:
        account_ids = [a["id"] for a in (await accounts(c))["accounts"] if a["type"] == INVESTMENT_TYPE]
    if not account_ids:
        return {"account_ids": [], "count": 0, "total_value": 0, "holdings": []}
    as_of = (today or date.today()).isoformat()
    edges = ((((await c.holdings(account_ids, as_of)).get("portfolio") or {})
              .get("aggregateHoldings") or {}).get("edges") or [])
    out = []
    for e in edges:
        n = e.get("node") or {}
        sec = n.get("security") or {}
        first = (n.get("holdings") or [{}])[0] or {}  # manual holdings have no security
        value, basis = n.get("totalValue"), n.get("basis")
        out.append({
            "ticker": sec.get("ticker") or first.get("ticker"),
            "name": sec.get("name") or first.get("name"),
            "type": sec.get("typeDisplay") or first.get("typeDisplay"),
            "quantity": n.get("quantity"),
            "price": sec.get("currentPrice"),
            "value": value,
            "cost_basis": basis,
            "gain": round(value - basis, 2) if value is not None and basis else None,
        })
    out.sort(key=lambda h: -(h["value"] or 0))
    return {"account_ids": account_ids, "count": len(out),
            "total_value": round(sum(h["value"] or 0 for h in out), 2), "holdings": out}


async def net_worth(c: MonarchClient, start: str, end: str, daily: bool = False) -> dict[str, Any]:
    """Net worth over time: the last snapshot of each month (or every day), plus the change."""
    snaps = sorted(((s.get("date"), s.get("balance")) for s in
                    (await c.net_worth(start, end)).get("aggregateSnapshots") or []
                    if s.get("date") and start <= s["date"] <= end), key=lambda s: s[0])
    if not daily:
        by_month: dict[str, tuple[str, Any]] = {}
        for d, b in snaps:
            by_month[d[:7]] = (d, b)  # sorted, so the last one per month wins
        snaps = list(by_month.values())
    points = [{"date": d, "net_worth": b} for d, b in snaps]
    first = points[0]["net_worth"] if points else None
    last = points[-1]["net_worth"] if points else None
    change = round(last - first, 2) if first is not None and last is not None else None
    return {"start_date": start, "end_date": end, "interval": "day" if daily else "month",
            "start_net_worth": first, "end_net_worth": last, "change": change, "points": points}
