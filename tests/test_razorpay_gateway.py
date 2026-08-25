"""Unit tests for the Razorpay gateway (Phase 2) — fully offline via a fake client.

Covers the parts that are pure logic and safety-critical: the status mapping, the
honest error taxonomy (definite → FAILED, ambiguous → UNKNOWN), gateway-side
idempotency, the deterministic ≤40-char receipt, webhook-signature verification
(reproducing Razorpay's exact HMAC-SHA256), and secret redaction.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.services.payment import (
    PAYMENT_CONFIRMED,
    PAYMENT_FAILED,
    PAYMENT_PENDING,
    PAYMENT_UNKNOWN,
)
from app.services.razorpay_gateway import (
    _receipt_for,
    status_from_provider,
    verify_webhook_signature,
)
from tests.conftest import WEBHOOK_SECRET


# --------------------------------------------------------------------------- #
# status mapping — conservative by construction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "provider_status, expected",
    [
        ("captured", PAYMENT_CONFIRMED),
        ("paid", PAYMENT_CONFIRMED),
        ("CAPTURED", PAYMENT_CONFIRMED),  # case-insensitive
        ("failed", PAYMENT_FAILED),
        ("created", PAYMENT_PENDING),
        ("attempted", PAYMENT_PENDING),
        ("authorized", PAYMENT_PENDING),  # authorized != captured -> still pending
        ("", PAYMENT_PENDING),
        (None, PAYMENT_PENDING),
        ("something_new", PAYMENT_PENDING),  # unknown -> never silently promoted
    ],
)
def test_status_from_provider(provider_status, expected):
    assert status_from_provider(provider_status) == expected


# --------------------------------------------------------------------------- #
# receipt is deterministic and within Razorpay's 40-char cap
# --------------------------------------------------------------------------- #
def test_receipt_is_capped_at_40_chars_and_deterministic():
    identity = "txid_" + "a" * 64  # 69 chars, like a real transaction_identity
    r1 = _receipt_for(identity)
    r2 = _receipt_for(identity)
    assert r1 == r2  # deterministic
    assert len(r1) <= 40
    assert identity.startswith(r1)  # a stable prefix of the identity


# --------------------------------------------------------------------------- #
# create_payment: success -> PENDING, money NOT captured at order creation
# --------------------------------------------------------------------------- #
def test_create_payment_success_is_pending(razorpay_gateway, rz_client, svc_session):
    result = razorpay_gateway.create_payment(
        svc_session, idempotency_key="txid_abc", amount=129900, currency="INR"
    )
    assert result.status == PAYMENT_PENDING
    assert result.provider == "razorpay"
    assert result.order_ref and result.order_ref.startswith("order_")
    assert result.payment_ref == ""  # nothing captured yet
    assert len(rz_client.create_calls) == 1
    # the semantic identity travels in notes; receipt is the ≤40-char slug
    payload = rz_client.create_calls[0]
    assert payload["notes"]["transaction_identity"] == "txid_abc"
    assert payload["partial_payment"] is False


def test_create_payment_is_idempotent_reuses_order(razorpay_gateway, rz_client, svc_session):
    first = razorpay_gateway.create_payment(
        svc_session, idempotency_key="txid_dup", amount=5000, currency="INR"
    )
    svc_session.commit()
    second = razorpay_gateway.create_payment(
        svc_session, idempotency_key="txid_dup", amount=5000, currency="INR"
    )
    assert second.idempotent_replay is True
    assert second.order_ref == first.order_ref
    # crucially: only ONE order was ever created on Razorpay's side
    assert len(rz_client.create_calls) == 1


def test_create_payment_definite_error_is_failed(razorpay_gateway, rz_client, svc_session):
    # A well-formed rejection before any money can move -> safe FAILED (no order).
    from razorpay.errors import BadRequestError

    rz_client.raise_on_create = BadRequestError("amount too small")
    result = razorpay_gateway.create_payment(
        svc_session, idempotency_key="txid_bad", amount=1, currency="INR"
    )
    assert result.status == PAYMENT_FAILED
    assert result.order_ref is None


def test_create_payment_ambiguous_error_is_unknown(razorpay_gateway, rz_client, svc_session):
    # Gateway/server/network/timeout -> UNKNOWN, never FAILED (we don't know).
    rz_client.raise_on_create = RuntimeError("connection reset")
    result = razorpay_gateway.create_payment(
        svc_session, idempotency_key="txid_unk", amount=5000, currency="INR"
    )
    assert result.status == PAYMENT_UNKNOWN
    assert result.order_ref is None


def test_create_payment_amount_is_integer_minor_units(razorpay_gateway, rz_client, svc_session):
    razorpay_gateway.create_payment(
        svc_session, idempotency_key="txid_int", amount=129900, currency="INR"
    )
    assert rz_client.create_calls[0]["amount"] == 129900
    assert isinstance(rz_client.create_calls[0]["amount"], int)


# --------------------------------------------------------------------------- #
# webhook signature — reproduce Razorpay's exact HMAC-SHA256 over the raw body
# --------------------------------------------------------------------------- #
def test_verify_webhook_signature_accepts_valid():
    body = b'{"event":"payment.captured"}'
    good = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret=WEBHOOK_SECRET, body=body, signature=good) is True


def test_verify_webhook_signature_rejects_tampered_body():
    body = b'{"event":"payment.captured"}'
    good = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    tampered = b'{"event":"payment.captured","extra":1}'
    assert verify_webhook_signature(secret=WEBHOOK_SECRET, body=tampered, signature=good) is False


def test_verify_webhook_signature_rejects_wrong_secret():
    body = b"{}"
    good = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret="other_secret", body=body, signature=good) is False


def test_verify_webhook_signature_rejects_empty():
    assert verify_webhook_signature(secret="", body=b"{}", signature="x") is False
    assert verify_webhook_signature(secret=WEBHOOK_SECRET, body=b"{}", signature="") is False


def test_instance_verify_uses_configured_secret(razorpay_gateway):
    body = b'{"hello":"world"}'
    good = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert razorpay_gateway.verify_webhook_signature(body, good) is True
    assert razorpay_gateway.verify_webhook_signature(body, "deadbeef") is False


# --------------------------------------------------------------------------- #
# secrets never leak
# --------------------------------------------------------------------------- #
def test_repr_never_exposes_secrets(razorpay_gateway):
    text = repr(razorpay_gateway)
    assert "secret_x" not in text
    assert WEBHOOK_SECRET not in text
    assert "rzp_test_x" in text  # the public key id is fine to show
