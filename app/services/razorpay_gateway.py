"""The real Razorpay **Test Mode** gateway (Phase 2).

This is the *only* module that talks to the Razorpay SDK. Everything Razorpay —
credentials, order/payment/refund calls, webhook-signature maths — is contained
here so the rest of TrustRail never imports the SDK and never sees a secret.

Design decisions (all deliberate, all documented):

* **Money is never captured at order-creation time.** ``create_payment`` creates
  a Razorpay *Order* and returns :data:`PAYMENT_PENDING`. The customer pays
  against that order out-of-band; TrustRail learns the real outcome
  asynchronously — via the ``payment.captured`` / ``payment.failed`` webhook, or
  by an authoritative reconciliation sweep. This is what makes the
  ``PAYMENT_PENDING → CONFIRMED/UNKNOWN`` boundary real rather than cosmetic.

* **Honest error taxonomy.** A *definitive* client rejection (``BadRequestError``)
  before any money can move → :data:`PAYMENT_FAILED` (safe: no order, no charge).
  An *ambiguous* failure (gateway/server/network/timeout) → :data:`PAYMENT_UNKNOWN`.
  We never downgrade an ambiguous failure to FAILED, and UNKNOWN never triggers
  another charge attempt.

* **Gateway-side idempotency.** Razorpay has no create-order idempotency key, so
  we persist ``transaction_identity → razorpay_order_id`` in
  :class:`~app.models.merchant.RazorpayPayment`. A repeated ``create_payment``
  for the same identity *reuses* the existing order instead of creating a second
  one. (Retrying order creation could not double-charge anyway — orders do not
  move money — but reuse keeps the linkage stable for reconciliation.)

* **Identity mapping.** ``transaction_identity`` (69 chars) is the semantic
  identity; it is written into the Razorpay order ``notes`` and mirrored in our
  own table. The Razorpay ``receipt`` field caps at 40 chars, so we pass a
  deterministic ≤40-char slug of the identity. Razorpay's own IDs are *never*
  trusted as identity — a webhook/reconciliation must validate amount, currency
  and order linkage before any state change.

* **Secrets never leak.** The key secret and webhook secret live only on this
  object; they are never returned, logged, put in an exception message, or
  written to an audit event. :meth:`__repr__` is redacted.

* **No floating point.** Amounts are integer minor units (paise) throughout.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from sqlalchemy.orm import Session

from app.models.merchant import RazorpayPayment
from app.services.payment import (
    PAYMENT_CONFIRMED,
    PAYMENT_FAILED,
    PAYMENT_PENDING,
    PAYMENT_UNKNOWN,
    PaymentResult,
)

PROVIDER = "razorpay"

# Razorpay's ``receipt`` field is capped at 40 characters.
_RECEIPT_MAX = 40

# Provider-reported statuses that map onto our vocabulary. Anything not listed
# here (``created`` / ``attempted`` / ``authorized`` …) is still in flight and
# maps to PENDING — money has not been confirmed captured.
_PROVIDER_CONFIRMED = {"captured", "paid"}
_PROVIDER_FAILED = {"failed"}


def status_from_provider(provider_status: str | None) -> str:
    """Map a Razorpay order/payment status onto TrustRail's payment vocabulary.

    Conservative by construction: only an explicit captured/paid maps to
    CONFIRMED and only an explicit failed maps to FAILED; everything else is
    treated as still-PENDING (never silently promoted or failed).
    """
    s = (provider_status or "").lower()
    if s in _PROVIDER_CONFIRMED:
        return PAYMENT_CONFIRMED
    if s in _PROVIDER_FAILED:
        return PAYMENT_FAILED
    return PAYMENT_PENDING


def _receipt_for(idempotency_key: str) -> str:
    """A deterministic, ≤40-char Razorpay receipt derived from the identity.

    The full ``transaction_identity`` goes in the order ``notes``; the receipt is
    only a merchant-side reference for the Razorpay dashboard, so a stable prefix
    of the identity is sufficient and collision-safe in practice.
    """
    return idempotency_key[:_RECEIPT_MAX]


def verify_webhook_signature(*, secret: str, body: bytes, signature: str) -> bool:
    """Constant-time verification of a Razorpay webhook signature.

    Razorpay signs the **raw request body** with HMAC-SHA256 keyed by the webhook
    secret and sends the hex digest in the ``X-Razorpay-Signature`` header. This
    reproduces that exact algorithm with the standard library (identical to
    ``razorpay.Client().utility.verify_webhook_signature``), which keeps the
    security-critical check fully unit-testable offline and free of any SDK
    dependency on the hot path.
    """
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class RazorpayGateway:
    """A :class:`~app.services.payment.PaymentGateway` backed by Razorpay Test Mode.

    Beyond the ``create_payment`` protocol method it exposes the read/capture/
    refund primitives the webhook, reconciliation and refund services need, so
    that every Razorpay API call funnels through one credential-holding object.
    """

    provider = PROVIDER

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        webhook_secret: str = "",
        client: Any | None = None,
    ) -> None:
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret

        # Lazily construct the SDK client so importing this module (e.g. in a
        # unit test that injects a fake client) never forces a real client or a
        # network dependency. ``client`` is the seam tests inject through.
        if client is not None:
            self._client = client
        else:  # pragma: no cover - exercised only by network-gated contract tests
            import razorpay

            self._client = razorpay.Client(auth=(key_id, key_secret))

        # Resolve the SDK's error classes once, for the create-time taxonomy.
        # Guarded so a fake-client unit test works even without the SDK present.
        try:  # pragma: no cover - trivial import shim
            from razorpay.errors import (
                BadRequestError,
                GatewayError,
                ServerError,
            )

            self._definite_error: tuple[type[BaseException], ...] = (BadRequestError,)
            self._ambiguous_error: tuple[type[BaseException], ...] = (
                GatewayError,
                ServerError,
            )
        except Exception:  # pragma: no cover
            self._definite_error = ()
            self._ambiguous_error = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Never expose secrets. Show only the (public) key id.
        return f"<RazorpayGateway key_id={self._key_id!r} test_mode>"

    # ---- PaymentGateway protocol ----------------------------------------- #
    def create_payment(
        self,
        db: Session,
        *,
        idempotency_key: str,
        amount: int,
        currency: str,
        force_decline: bool = False,
    ) -> PaymentResult:
        """Create (or reuse) a Razorpay Order. Returns PENDING on success.

        ``force_decline`` is a mock-only demo hook and has no meaning here: with a
        real gateway the decline happens when the customer pays, not when we open
        the order, so it is intentionally ignored.
        """
        amount = int(amount)  # minor units; never float

        # Idempotent reuse: if we already opened an order for this identity, hand
        # back its current state instead of creating a second order.
        existing = db.get(RazorpayPayment, idempotency_key)
        if existing is not None:
            return PaymentResult(
                status=status_from_provider(existing.status),
                payment_ref=existing.razorpay_payment_id or "",
                amount=existing.amount,
                currency=existing.currency,
                idempotent_replay=True,
                order_ref=existing.razorpay_order_id,
                provider=PROVIDER,
            )

        order_payload = {
            "amount": amount,
            "currency": currency,
            "receipt": _receipt_for(idempotency_key),
            "partial_payment": False,
            # The authoritative semantic identity travels in notes; it is what a
            # webhook/reconciliation validates against, not the Razorpay id.
            "notes": {"transaction_identity": idempotency_key},
        }

        try:
            order = self._client.order.create(order_payload)
        except self._definite_error:
            # A well-formed rejection *before* any money moves: no order exists,
            # so this is a clean, safe failure — not an uncertain one.
            return PaymentResult(
                status=PAYMENT_FAILED,
                payment_ref="",
                amount=amount,
                currency=currency,
                provider=PROVIDER,
            )
        except Exception:
            # Gateway/server/network/timeout: we cannot know whether an order was
            # created. UNKNOWN, never FAILED — and the caller must not re-charge.
            return PaymentResult(
                status=PAYMENT_UNKNOWN,
                payment_ref="",
                amount=amount,
                currency=currency,
                provider=PROVIDER,
            )

        order_id = str(order["id"])
        db.add(
            RazorpayPayment(
                idempotency_key=idempotency_key,
                razorpay_order_id=order_id,
                amount=amount,
                currency=currency,
                status=str(order.get("status") or "created"),
            )
        )
        db.flush()

        return PaymentResult(
            status=PAYMENT_PENDING,
            payment_ref="",
            amount=amount,
            currency=currency,
            order_ref=order_id,
            provider=PROVIDER,
        )

    # ---- authoritative reads (used by reconciliation / webhook) ---------- #
    def fetch_order(self, order_id: str) -> dict:
        """Fetch an order entity (``{id, status, amount, amount_paid, …}``)."""
        return dict(self._client.order.fetch(order_id))

    def list_order_payments(self, order_id: str) -> list[dict]:
        """Return the payment entities attached to an order (may be empty)."""
        resp = self._client.order.payments(order_id)
        return [dict(p) for p in (resp.get("items") or [])]

    def fetch_payment(self, payment_id: str) -> dict:
        """Fetch a single payment entity (``{id, order_id, status, amount, …}``)."""
        return dict(self._client.payment.fetch(payment_id))

    # ---- capture (TrustRail-controlled; never the AI) -------------------- #
    def capture_payment(self, payment_id: str, amount: int, currency: str) -> dict:
        """Capture an authorized payment for exactly ``amount`` minor units."""
        return dict(self._client.payment.capture(payment_id, int(amount), {"currency": currency}))

    # ---- refund (used by the refund service) ----------------------------- #
    def refund_payment(
        self,
        payment_id: str,
        *,
        amount: int,
        notes: dict | None = None,
        receipt: str | None = None,
    ) -> dict:
        """Issue a refund against a captured payment. Returns the refund entity.

        Idempotency of *TrustRail's* refund is guaranteed by the persisted
        refund id / state on our side (see :mod:`app.services.refund`); the
        pinned SDK does not reliably expose a per-call idempotency header.
        """
        data: dict[str, Any] = {"amount": int(amount), "speed": "normal"}
        if notes:
            data["notes"] = notes
        if receipt:
            data["receipt"] = receipt
        return dict(self._client.payment.refund(payment_id, data))

    def fetch_refund(self, refund_id: str) -> dict:
        return dict(self._client.refund.fetch(refund_id))

    # ---- webhook signature ----------------------------------------------- #
    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        """Verify a webhook payload against the configured webhook secret."""
        return verify_webhook_signature(
            secret=self._webhook_secret, body=body, signature=signature
        )
