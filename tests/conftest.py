"""Shared pytest fixtures.

Each test runs against a *fresh in-memory SQLite database* with a *frozen clock*,
so results are fully deterministic and hermetic. We never touch the real
file-backed database or the wall clock. The FastAPI app's lifespan is
intentionally not entered (we drive the DB directly through dependency
overrides), which keeps tests from creating stray database files.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.clock import Clock, get_clock
from app.db import Base, get_db
from app.main import app as fastapi_app
from app.merchant.catalogue import MERCHANT_ID, seed_merchant
from app.services.gateway import get_gateway
from app.services.payment import default_gateway
from app.services.razorpay_gateway import RazorpayGateway

FROZEN_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

# A fixed webhook secret for the hermetic Phase 2 tests. Tests reproduce the exact
# HMAC-SHA256 Razorpay uses, so signature verification is exercised fully offline.
WEBHOOK_SECRET = "whsec_test_secret"


class FrozenClock(Clock):
    """A clock the tests control explicitly."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


@pytest.fixture
def frozen_now() -> datetime:
    return FROZEN_NOW


@pytest.fixture
def clock(frozen_now: datetime) -> FrozenClock:
    return FrozenClock(frozen_now)


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def SessionFactory(db_engine):
    return sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


@pytest.fixture
def db_session(SessionFactory) -> Session:
    session = SessionFactory()
    seed_merchant(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(SessionFactory, clock) -> TestClient:
    # Seed the shared in-memory DB once.
    seed = SessionFactory()
    seed_merchant(seed)
    seed.close()

    def override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_clock] = lambda: clock
    # Always use mock gateway in tests, even when .env sets PAYMENT_GATEWAY=razorpay
    fastapi_app.dependency_overrides[get_gateway] = lambda: default_gateway

    # NOTE: no `with TestClient(...)` — we skip the lifespan on purpose.
    test_client = TestClient(fastapi_app)
    try:
        yield test_client
    finally:
        fastapi_app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# payload builders
# --------------------------------------------------------------------------- #
@pytest.fixture
def make_payload():
    """Expose the payload builder to tests as a callable fixture."""
    return intent_payload


def intent_payload(
    *,
    agent_id: str = "agent-openai-buyer-1",
    merchant_id: str = MERCHANT_ID,
    items: list[dict] | None = None,
    max_amount: int = 500000,  # ₹5,000.00
    currency: str = "INR",
    max_quantity: int = 1,
    expires_at: datetime | None = None,
) -> dict:
    """Build a valid PurchaseIntent body. Defaults describe an allowable purchase."""
    if items is None:
        items = [{"sku": "SKU-001", "quantity": 1}]
    if expires_at is None:
        expires_at = FROZEN_NOW + timedelta(hours=1)
    return {
        "agent_id": agent_id,
        "merchant_id": merchant_id,
        "items": items,
        "constraints": {
            "max_amount": max_amount,
            "currency": currency,
            "max_quantity": max_quantity,
        },
        "authorization": {"expires_at": expires_at.isoformat()},
    }


# --------------------------------------------------------------------------- #
# Phase 2 — a programmable, offline stand-in for razorpay.Client
# --------------------------------------------------------------------------- #
class FakeRazorpayClient:
    """In-memory stand-in for ``razorpay.Client`` — no network, fully controllable.

    Records every call (``create_calls`` / ``capture_calls`` / ``refund_calls``)
    so tests can assert *exactly* how many times TrustRail hit the gateway (e.g.
    "no second refund"). ``raise_on_create`` lets a test simulate a definite
    (``BadRequestError`` → FAILED) or ambiguous (any other error → UNKNOWN)
    order-creation failure.
    """

    class _Orders:
        def __init__(self, outer: FakeRazorpayClient) -> None:
            self._o = outer

        def create(self, payload: dict) -> dict:
            self._o.create_calls.append(payload)
            if self._o.raise_on_create is not None:
                raise self._o.raise_on_create
            self._o._n += 1
            oid = f"order_{self._o._n}"
            entity = {
                "id": oid,
                "status": "created",
                "amount": payload["amount"],
                "currency": payload["currency"],
                "amount_paid": 0,
                "receipt": payload.get("receipt"),
                "notes": payload.get("notes", {}),
            }
            self._o.orders[oid] = entity
            self._o.payments.setdefault(oid, [])
            return dict(entity)

        def fetch(self, order_id: str) -> dict:
            return dict(self._o.orders[order_id])

        def payments(self, order_id: str) -> dict:
            return {"items": [dict(p) for p in self._o.payments.get(order_id, [])]}

    class _Payments:
        def __init__(self, outer: FakeRazorpayClient) -> None:
            self._o = outer

        def fetch(self, payment_id: str) -> dict:
            return dict(self._o.payment_by_id[payment_id])

        def capture(self, payment_id: str, amount: int, data: dict) -> dict:
            self._o.capture_calls.append((payment_id, amount, dict(data)))
            entity = {"id": payment_id, "status": "captured", "amount": amount}
            self._o.payment_by_id[payment_id] = entity
            return dict(entity)

        def refund(self, payment_id: str, data: dict) -> dict:
            self._o.refund_calls.append((payment_id, dict(data)))
            if self._o.raise_on_refund is not None:
                raise self._o.raise_on_refund
            self._o._rn += 1
            return {
                "id": f"rfnd_{self._o._rn}",
                "status": "processed",
                "payment_id": payment_id,
                "amount": data.get("amount"),
            }

    class _Refunds:
        def __init__(self, outer: FakeRazorpayClient) -> None:
            self._o = outer

        def fetch(self, refund_id: str) -> dict:
            return {"id": refund_id, "status": "processed"}

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self.payments: dict[str, list[dict]] = {}
        self.payment_by_id: dict[str, dict] = {}
        self._n = 0
        self._rn = 0
        self.raise_on_create: BaseException | None = None
        self.raise_on_refund: BaseException | None = None
        self.create_calls: list[dict] = []
        self.capture_calls: list[tuple] = []
        self.refund_calls: list[tuple] = []
        self.order = self._Orders(self)
        self.payment = self._Payments(self)
        self.refund = self._Refunds(self)

    # -- test helpers to attach payments to an order -- #
    def add_captured(self, order_id, payment_id="pay_cap", amount=None, currency="INR"):
        amt = self.orders[order_id]["amount"] if amount is None else amount
        p = {
            "id": payment_id,
            "status": "captured",
            "amount": amt,
            "currency": currency,
            "order_id": order_id,
        }
        self.payments.setdefault(order_id, []).append(p)
        self.payment_by_id[payment_id] = p
        return p

    def add_authorized(
        self, order_id, payment_id="pay_auth", amount=None, currency="INR"
    ):
        amt = self.orders[order_id]["amount"] if amount is None else amount
        p = {
            "id": payment_id,
            "status": "authorized",
            "amount": amt,
            "currency": currency,
            "order_id": order_id,
        }
        self.payments.setdefault(order_id, []).append(p)
        self.payment_by_id[payment_id] = p
        return p


@pytest.fixture
def rz_client() -> FakeRazorpayClient:
    return FakeRazorpayClient()


@pytest.fixture
def razorpay_gateway(rz_client: FakeRazorpayClient) -> RazorpayGateway:
    return RazorpayGateway(
        key_id="rzp_test_x",
        key_secret="secret_x",
        webhook_secret=WEBHOOK_SECRET,
        client=rz_client,
    )


@pytest.fixture
def svc_session(SessionFactory) -> Session:
    """A plain Session on the shared in-memory engine, for driving services directly.

    Does not re-seed the merchant (the ``client_rz`` fixture already seeds it), so
    it is safe to combine with the HTTP client in one test.
    """
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client_rz(SessionFactory, clock, razorpay_gateway) -> TestClient:
    """A TestClient wired to the real RazorpayGateway (backed by the fake client)."""
    seed = SessionFactory()
    seed_merchant(seed)
    seed.close()

    def override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_clock] = lambda: clock
    fastapi_app.dependency_overrides[get_gateway] = lambda: razorpay_gateway

    test_client = TestClient(fastapi_app)
    try:
        yield test_client
    finally:
        fastapi_app.dependency_overrides.clear()
