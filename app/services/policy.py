"""Phase 2 — the deterministic Policy Engine.

``evaluate()`` is a **pure function**: given a fully-resolved context it returns
the same structured decision every time. It performs no I/O and never sees raw
LLM text — the orchestrator assembles the context from the merchant quote, the
authorized constraints and the clock. This is the guarantee that the LLM can
*propose* but never *authorize*.

Every decision is explainable: each check is reported with pass/fail and a
human-readable detail, and the blocking reason is always the first failing check
in a fixed evaluation order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.clock import ensure_aware_utc
from app.enums import Decision, PolicyPhase, TransactionState
from app.money import format_amount
from app.schemas.policy import PolicyCheckOut, PolicyDecisionOut

# Check identifiers.
CHECK_MERCHANT = "merchant_known"
CHECK_SKUS = "skus_valid"
CHECK_CURRENCY = "currency_match"
CHECK_EXPIRY = "authorization_not_expired"
CHECK_QUANTITY = "quantity_within_limit"
CHECK_AMOUNT = "amount_within_authorized_max"
CHECK_INVENTORY = "inventory_available"
CHECK_PRICE = "price_unchanged"

# Fixed evaluation order. Determines which reason "wins" if several checks fail.
CHECK_ORDER = [
    CHECK_MERCHANT,
    CHECK_SKUS,
    CHECK_CURRENCY,
    CHECK_EXPIRY,
    CHECK_QUANTITY,
    CHECK_AMOUNT,
    CHECK_INVENTORY,
    CHECK_PRICE,
]

# Which transaction state a failed check *wants* to move to. The orchestrator
# guards this against the state machine (an unreachable target falls back to
# POLICY_BLOCKED), so e.g. an out-of-stock item at first validation becomes
# POLICY_BLOCKED, while the same failure after AUTHORIZED becomes INVENTORY_CHANGED.
CHECK_TO_STATE = {
    CHECK_MERCHANT: TransactionState.INVALID,
    CHECK_SKUS: TransactionState.INVALID,
    CHECK_CURRENCY: TransactionState.POLICY_BLOCKED,
    CHECK_EXPIRY: TransactionState.AUTH_EXPIRED,
    CHECK_QUANTITY: TransactionState.POLICY_BLOCKED,
    CHECK_AMOUNT: TransactionState.POLICY_BLOCKED,
    CHECK_INVENTORY: TransactionState.INVENTORY_CHANGED,
    CHECK_PRICE: TransactionState.PRICE_CHANGED,
}


@dataclass
class PolicyContext:
    phase: PolicyPhase
    merchant_id_intent: str
    merchant_known: bool
    currency_intent: str
    merchant_currency: str | None
    currency_conflict: bool
    unknown_skus: list[str]
    oos_skus: list[str]  # known SKUs with insufficient inventory
    all_available: bool
    order_total: int
    max_amount: int
    total_quantity: int
    max_quantity: int
    now: datetime
    expires_at: datetime
    is_authorized: bool = False
    prior_quoted_total: int | None = None


@dataclass
class PolicyResult:
    decision: Decision
    reason: str
    checks: list[PolicyCheckOut] = field(default_factory=list)
    failed_check: str | None = None

    def as_schema(self) -> PolicyDecisionOut:
        return PolicyDecisionOut(
            decision=self.decision, reason=self.reason, policy_checks=self.checks
        )


def evaluate(ctx: PolicyContext) -> PolicyResult:
    """Evaluate every policy check and return a deterministic structured decision."""
    cur = ctx.currency_intent
    now = ensure_aware_utc(ctx.now)
    expires = ensure_aware_utc(ctx.expires_at)

    passed: dict[str, bool] = {}
    detail: dict[str, str] = {}

    # merchant
    passed[CHECK_MERCHANT] = ctx.merchant_known
    detail[CHECK_MERCHANT] = (
        f"merchant '{ctx.merchant_id_intent}' recognised"
        if ctx.merchant_known
        else f"unknown/unauthorised merchant '{ctx.merchant_id_intent}'"
    )

    # SKUs
    passed[CHECK_SKUS] = not ctx.unknown_skus
    detail[CHECK_SKUS] = (
        "all SKUs exist in merchant catalogue"
        if not ctx.unknown_skus
        else f"unknown SKUs: {', '.join(ctx.unknown_skus)}"
    )

    # currency
    currency_ok = (not ctx.currency_conflict) and (
        ctx.merchant_currency is None or ctx.merchant_currency == cur
    )
    passed[CHECK_CURRENCY] = currency_ok
    if ctx.currency_conflict:
        detail[CHECK_CURRENCY] = "basket mixes multiple currencies"
    elif ctx.merchant_currency and ctx.merchant_currency != cur:
        detail[CHECK_CURRENCY] = (
            f"intent currency {cur} != merchant currency {ctx.merchant_currency}"
        )
    else:
        detail[CHECK_CURRENCY] = f"currency {cur} matches merchant"

    # authorization expiry
    not_expired = now < expires
    passed[CHECK_EXPIRY] = not_expired
    detail[CHECK_EXPIRY] = (
        f"authorization valid until {expires.isoformat()}"
        if not_expired
        else f"authorization expired at {expires.isoformat()} (now {now.isoformat()})"
    )

    # quantity ceiling
    qty_ok = ctx.total_quantity <= ctx.max_quantity
    passed[CHECK_QUANTITY] = qty_ok
    detail[CHECK_QUANTITY] = (
        f"total quantity {ctx.total_quantity} within authorized max {ctx.max_quantity}"
        if qty_ok
        else f"total quantity {ctx.total_quantity} exceeds authorized max {ctx.max_quantity}"
    )

    # amount ceiling — the headline gate
    amount_ok = ctx.order_total <= ctx.max_amount
    passed[CHECK_AMOUNT] = amount_ok
    detail[CHECK_AMOUNT] = (
        f"order total {format_amount(ctx.order_total, cur)} within authorized "
        f"maximum {format_amount(ctx.max_amount, cur)}"
        if amount_ok
        else f"transaction total {format_amount(ctx.order_total, cur)} exceeds "
        f"authorized maximum {format_amount(ctx.max_amount, cur)}"
    )

    # inventory
    passed[CHECK_INVENTORY] = ctx.all_available
    detail[CHECK_INVENTORY] = (
        "requested quantities are in stock"
        if ctx.all_available
        else f"insufficient inventory for: {', '.join(ctx.oos_skus) or 'unknown items'}"
    )

    # price stability (meaningful only once we have a prior quote)
    price_ok = ctx.prior_quoted_total is None or ctx.order_total == ctx.prior_quoted_total
    passed[CHECK_PRICE] = price_ok
    detail[CHECK_PRICE] = (
        "price unchanged since validation"
        if price_ok
        else f"price changed: quoted {format_amount(ctx.prior_quoted_total or 0, cur)}, "
        f"now {format_amount(ctx.order_total, cur)}"
    )

    checks = [
        PolicyCheckOut(name=name, passed=passed[name], detail=detail[name])
        for name in CHECK_ORDER
    ]

    failed = next((name for name in CHECK_ORDER if not passed[name]), None)
    if failed is not None:
        return PolicyResult(
            decision=Decision.BLOCK,
            reason=detail[failed],
            checks=checks,
            failed_check=failed,
        )

    if ctx.phase == PolicyPhase.EXECUTE and not ctx.is_authorized:
        return PolicyResult(
            decision=Decision.REQUIRES_AUTHORIZATION,
            reason="purchase satisfies all checks but has no live authorization",
            checks=checks,
        )

    return PolicyResult(
        decision=Decision.ALLOW, reason="all policy checks passed", checks=checks
    )
