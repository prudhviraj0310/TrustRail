"""The Transaction aggregate — the single stateful entity in TrustRail.

There is exactly **one** Transaction per ``transaction_identity`` (enforced by a
unique constraint). Many identical intents therefore collapse onto one
transaction, which is the foundation of idempotency.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.enums import TransactionState


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Deterministic idempotency key derived from the canonical intent.
    transaction_identity: Mapped[str] = mapped_column(
        String(96), unique=True, index=True, nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), default=TransactionState.INTENT_CREATED.value, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False)

    # The maximum amount the user authorized (integer minor units). Copied from
    # the intent's constraints so policy re-checks never trust live LLM input.
    authorized_max_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # The merchant's quoted order total, captured at validation time. A later
    # re-quote that differs indicates PRICE_CHANGED.
    quoted_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    authorized_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    authorizing_intent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    payment_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merchant_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_captured: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # ---- Phase 2: Razorpay linkage --------------------------------------- #
    # Populated only when PAYMENT_GATEWAY=razorpay. TrustRail's
    # ``transaction_identity`` remains the *semantic* identity; these persist the
    # provider-side references so an asynchronous webhook or a reconciliation
    # sweep can map an external event back to THIS transaction and validate it
    # (amount/currency/order) before any state change. Never trusted as identity.
    payment_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    razorpay_order_id: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    razorpay_refund_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Last provider-reported payment status (created/authorized/captured/failed/
    # refunded). Diagnostic only — the TrustRail state column is authoritative.
    payment_status: Mapped[str | None] = mapped_column(String(24), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Transaction {self.id} state={self.state}>"
