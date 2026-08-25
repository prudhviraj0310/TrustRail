"""Canonical enumerations shared across the whole system.

Keeping these in one place makes the state machine and audit trail easy to read
and impossible to typo.
"""

from __future__ import annotations

from enum import StrEnum


class TransactionState(StrEnum):
    """Every state a transaction can occupy.

    Happy path first, then failure/recovery states. Transitions between them are
    defined explicitly in ``app.services.state_machine`` — this enum only names
    the states, it does not permit any movement between them.
    """

    # --- happy path ---
    INTENT_CREATED = "INTENT_CREATED"
    VALIDATED = "VALIDATED"
    AUTHORIZED = "AUTHORIZED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    COMPLETED = "COMPLETED"

    # --- failure / recovery ---
    INVALID = "INVALID"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    INVENTORY_CHANGED = "INVENTORY_CHANGED"
    PRICE_CHANGED = "PRICE_CHANGED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_UNKNOWN = "PAYMENT_UNKNOWN"
    ORDER_FAILED = "ORDER_FAILED"
    RECOVERY_PENDING = "RECOVERY_PENDING"
    REFUND_REQUIRED = "REFUND_REQUIRED"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRES_AUTHORIZATION = "REQUIRES_AUTHORIZATION"


class PolicyPhase(StrEnum):
    """Which stage of the lifecycle a policy evaluation is running for."""

    VALIDATE = "VALIDATE"
    AUTHORIZE = "AUTHORIZE"
    EXECUTE = "EXECUTE"


class Actor(StrEnum):
    AI_BUYER = "AI_BUYER"
    POLICY_ENGINE = "POLICY_ENGINE"
    MERCHANT = "MERCHANT"
    PAYMENT_GATEWAY = "PAYMENT_GATEWAY"  # mock in Phase 1
    RAZORPAY = "RAZORPAY"  # concrete gateway in Phase 2
    TRUSTRAIL = "TRUSTRAIL"
    SYSTEM = "SYSTEM"


class AuditResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    BLOCKED = "BLOCKED"
    INFO = "INFO"


class IntentStatus(StrEnum):
    """Lifecycle of a single submitted intent (the request envelope)."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    AUTHORIZED = "AUTHORIZED"
    BLOCKED = "BLOCKED"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"  # a transaction was executed from this intent
