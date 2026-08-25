"""Merchant-side persistence.

These tables model the *external* merchant system that TrustRail must
coordinate with — a deliberately separate concern from TrustRail's own
transaction state. In Phase 1 they live in the same database for convenience;
the ``MerchantClient`` seam (app/merchant/client.py) means they could just as
easily sit behind an HTTP boundary.

``MockPayment`` belongs to the payment-gateway seam (app/services/payment.py),
not the merchant, but is colocated here as it is likewise a stand-in for an
external system (Razorpay in Phase 2).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MerchantProduct(Base):
    __tablename__ = "merchant_products"

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)  # minor units
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    inventory: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_info: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Deterministic failure hooks so the recovery states are demonstrable/testable.
    force_payment_decline: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    force_order_failure: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class MerchantOrder(Base):
    __tablename__ = "merchant_orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Idempotency key == transaction_identity. A repeated create with the same
    # key returns the existing order instead of creating a duplicate.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(96), unique=True, index=True, nullable=True
    )
    items: Mapped[list] = mapped_column(JSON, nullable=False)
    total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class MockPayment(Base):
    """Persistent record for the mock payment gateway (Phase-2: Razorpay).

    Keyed by idempotency key so the same semantic purchase never double-charges,
    even across process restarts.
    """

    __tablename__ = "mock_payments"

    idempotency_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    payment_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # CONFIRMED/FAILED
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class RazorpayPayment(Base):
    """Gateway-side idempotency record for the real Razorpay integration.

    Keyed by ``transaction_identity`` so that a repeated execute for the same
    semantic purchase reuses the same Razorpay **Order** instead of creating a
    second one (Razorpay itself has no create-order idempotency key). This is the
    provider-side mirror of :class:`MockPayment` and lives here for the same
    reason: it stands in for external-system bookkeeping, not TrustRail state.

    It never stores secrets — only the non-sensitive Razorpay identifiers and the
    last-seen provider status.
    """

    __tablename__ = "razorpay_payments"

    idempotency_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    razorpay_order_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    razorpay_refund_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    # Provider status: created/authorized/captured/failed/refunded.
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
