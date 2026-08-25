"""Row-level locking helpers for concurrency-safe recovery (Phase 2 / STEP 12).

The dangerous concurrency in TrustRail is two events racing to resolve the *same*
transaction — e.g. a ``payment.captured`` webhook and a reconciliation sweep both
firing for one order at the same instant. Without serialization both could read
``PAYMENT_PENDING`` and both try to confirm + fulfil, risking a double order.

On **PostgreSQL** (the intended source of truth) we take a ``SELECT … FOR UPDATE``
row lock on the transaction so the second worker blocks until the first commits,
then re-reads the now-updated state and takes the idempotent no-op path.

On **SQLite** (local/tests) ``FOR UPDATE`` is unsupported; we omit it. SQLite's
single-writer model plus the in-memory ``StaticPool`` used in tests already
serialize writers, so the tests remain deterministic. This is a documented
backend difference, not a silent no-op: the guarantee holds on Postgres.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.transaction import Transaction


def _locking(db: Session) -> bool:
    """True when the backend supports row locking (i.e. not SQLite)."""
    bind = db.get_bind()
    return bind.dialect.name != "sqlite"


def lock_transaction_by_identity(
    db: Session, transaction_identity: str
) -> Transaction | None:
    """Fetch a transaction by its semantic identity, locking the row on Postgres."""
    stmt = select(Transaction).where(
        Transaction.transaction_identity == transaction_identity
    )
    if _locking(db):
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def lock_transaction_by_order_id(
    db: Session, razorpay_order_id: str
) -> Transaction | None:
    """Fetch a transaction by its Razorpay order id, locking the row on Postgres."""
    stmt = select(Transaction).where(Transaction.razorpay_order_id == razorpay_order_id)
    if _locking(db):
        stmt = stmt.with_for_update()
    return db.scalar(stmt)
