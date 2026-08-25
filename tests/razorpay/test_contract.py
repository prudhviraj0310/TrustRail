"""Network-gated Razorpay **Test Mode** contract tests.

These are the only tests that touch the real Razorpay API. They are OFF by
default and never run in ``make test`` — the normal suite stays fully offline.

Enable explicitly::

    export RAZORPAY_CONTRACT_TESTS=1
    export RAZORPAY_KEY_ID=rzp_test_xxx
    export RAZORPAY_KEY_SECRET=xxx
    export RAZORPAY_WEBHOOK_SECRET=xxx          # optional (signature test)
    pytest tests/razorpay -v

If the flag is unset (or the credentials are absent) every test SKIPS cleanly,
so this file is safe to keep in the repo and safe to collect in CI.

They verify that our understanding of the SDK's real behaviour matches what
:mod:`app.services.razorpay_gateway` assumes: order creation returns an
``order_…`` id in ``created`` status, the semantic identity round-trips through
``notes``, a fresh order has no payments, and (if a webhook secret is set) the
SDK's own signature utility agrees with our stdlib implementation.
"""

from __future__ import annotations

import os

import pytest

_ENABLED = os.getenv("RAZORPAY_CONTRACT_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="Razorpay contract tests are network-gated; set RAZORPAY_CONTRACT_TESTS=1 to run.",
)


def _require_creds() -> tuple[str, str, str]:
    key_id = os.getenv("RAZORPAY_KEY_ID", "")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not (key_id and key_secret):
        pytest.skip("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set")
    if not key_id.startswith("rzp_test_"):
        pytest.skip("refusing to run contract tests against a non-test key")
    return key_id, key_secret, webhook_secret


@pytest.fixture
def gateway():
    key_id, key_secret, webhook_secret = _require_creds()
    from app.services.razorpay_gateway import RazorpayGateway

    return RazorpayGateway(
        key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret
    )


@pytest.fixture
def db_session():
    """A throwaway in-memory session so create_payment can persist linkage."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.models  # noqa: F401
    from app.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


# --------------------------------------------------------------------------- #
# contract assertions against real Test Mode
# --------------------------------------------------------------------------- #
def test_create_order_returns_created_and_roundtrips_identity(gateway, db_session):
    identity = "txid_contract_" + "0" * 8
    result = gateway.create_payment(
        db_session, idempotency_key=identity, amount=100, currency="INR"
    )
    from app.services.payment import PAYMENT_PENDING

    assert result.status == PAYMENT_PENDING
    assert result.order_ref and result.order_ref.startswith("order_")

    # fetch the order back and confirm the semantic identity round-trips in notes
    order = gateway.fetch_order(result.order_ref)
    assert order["id"] == result.order_ref
    assert order["status"] in {"created", "attempted", "paid"}
    assert order["amount"] == 100
    assert order.get("notes", {}).get("transaction_identity") == identity


def test_fresh_order_has_no_payments(gateway, db_session):
    result = gateway.create_payment(
        db_session, idempotency_key="txid_contract_empty_1", amount=100, currency="INR"
    )
    payments = gateway.list_order_payments(result.order_ref)
    assert payments == []  # nobody has paid a brand-new order


def test_create_payment_is_idempotent_against_real_api(gateway, db_session):
    identity = "txid_contract_idem_1"
    first = gateway.create_payment(
        db_session, idempotency_key=identity, amount=100, currency="INR"
    )
    db_session.commit()
    second = gateway.create_payment(
        db_session, idempotency_key=identity, amount=100, currency="INR"
    )
    # our persisted linkage reuses the same order rather than opening a second
    assert second.idempotent_replay is True
    assert second.order_ref == first.order_ref


def test_webhook_signature_matches_sdk_utility(gateway):
    webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not webhook_secret:
        pytest.skip("RAZORPAY_WEBHOOK_SECRET not set")

    import razorpay

    body = b'{"event":"payment.captured","contract":true}'
    client = razorpay.Client(auth=("x", "y"))
    sdk_sig = __import__("hmac").new(
        webhook_secret.encode(), body, __import__("hashlib").sha256
    ).hexdigest()

    # our implementation accepts a signature the SDK would also accept
    assert gateway.verify_webhook_signature(body, sdk_sig) is True
    # and the SDK's own verifier agrees with our signature
    client.utility.verify_webhook_signature(body.decode(), sdk_sig, webhook_secret)
