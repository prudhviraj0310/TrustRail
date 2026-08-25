"""The payment-gateway seam.

Phase 1 ships a deterministic **mock** gateway — this is explicitly NOT Razorpay
and invents no Razorpay APIs. Phase 2 adds a ``RazorpayGateway``
(:mod:`app.services.razorpay_gateway`) implementing the same
:class:`PaymentGateway` protocol; the orchestrator branches on the *status* of
the returned :class:`PaymentResult`, never on which gateway produced it.

Idempotency is keyed on the transaction identity and persisted, so the same
semantic purchase never double-charges — even across restarts. We claim
*idempotent, at-least-once* handling, never exactly-once distributed execution.

Payment status vocabulary (the whole point of Phase 2 is that the last two are
first-class, not error states):

* ``CONFIRMED`` — money captured; safe to fulfil.
* ``FAILED``    — definitively no charge; safe to stop.
* ``PENDING``   — a real gateway (Razorpay) accepted an *order* but money has
                  NOT moved yet; confirmation arrives asynchronously via webhook
                  or reconciliation. The mock never returns this.
* ``UNKNOWN``   — we could not learn the outcome (timeout / lost response). This
                  is NOT failure. It must NEVER trigger another blind charge; it
                  is resolved by authoritative reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.ids import new_payment_ref
from app.models.merchant import MockPayment

PAYMENT_CONFIRMED = "CONFIRMED"
PAYMENT_FAILED = "FAILED"
# Phase 2 — asynchronous / uncertain outcomes. UNKNOWN ≠ FAILED.
PAYMENT_PENDING = "PENDING"
PAYMENT_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PaymentResult:
    status: str  # CONFIRMED | FAILED | PENDING | UNKNOWN
    payment_ref: str
    amount: int
    currency: str
    idempotent_replay: bool = False
    # Phase 2: the gateway's order handle (Razorpay ``order_…``). Present when a
    # real gateway created an order whose payment resolves asynchronously. The
    # mock leaves this ``None``.
    order_ref: str | None = None
    # Which gateway produced this result ("mock" | "razorpay"). Recorded in the
    # audit trail so the story is explicit; defaults keep mock call-sites intact.
    provider: str = "mock"


class PaymentGateway(Protocol):
    def create_payment(
        self,
        db: Session,
        *,
        idempotency_key: str,
        amount: int,
        currency: str,
        force_decline: bool = False,
    ) -> PaymentResult: ...


class MockPaymentGateway:
    """Deterministic stand-in gateway (Phase 2: replace with RazorpayGateway).

    * ``force_decline=True`` -> deterministic FAILED (drives PAYMENT_FAILED).
    * otherwise -> deterministic CONFIRMED.
    * a repeated call with the same ``idempotency_key`` returns the original
      result without creating a second charge.
    """

    def create_payment(
        self,
        db: Session,
        *,
        idempotency_key: str,
        amount: int,
        currency: str,
        force_decline: bool = False,
    ) -> PaymentResult:
        existing = db.get(MockPayment, idempotency_key)
        if existing is not None:
            return PaymentResult(
                status=existing.status,
                payment_ref=existing.payment_ref,
                amount=existing.amount,
                currency=existing.currency,
                idempotent_replay=True,
            )

        status = PAYMENT_FAILED if force_decline else PAYMENT_CONFIRMED
        ref = new_payment_ref()
        record = MockPayment(
            idempotency_key=idempotency_key,
            payment_ref=ref,
            amount=amount,
            currency=currency,
            status=status,
        )
        db.add(record)
        db.flush()
        return PaymentResult(
            status=status, payment_ref=ref, amount=amount, currency=currency
        )


default_gateway: PaymentGateway = MockPaymentGateway()
