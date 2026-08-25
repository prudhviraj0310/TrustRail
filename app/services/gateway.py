"""Gateway selection (the Phase 2 dependency-injection seam).

The orchestrator, the webhook handler and the reconciliation/refund services all
obtain their :class:`~app.services.payment.PaymentGateway` from here, so which
concrete gateway is live is decided in exactly one place — driven by
``settings.payment_gateway``.

Crucially, the Razorpay gateway (and therefore the ``razorpay`` SDK) is imported
*lazily*, only when ``PAYMENT_GATEWAY=razorpay``. In the default mock mode this
module never touches the SDK, so the normal test suite stays offline and needs
no credentials.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.services.payment import PaymentGateway, default_gateway


def build_gateway(settings: Settings) -> PaymentGateway:
    """Construct the gateway named by ``settings.payment_gateway``.

    * ``mock``     → the process-wide deterministic :data:`default_gateway`.
    * ``razorpay`` → a :class:`RazorpayGateway` built from the server-side
      credentials. Raises if the mode is selected without full credentials, so a
      misconfiguration fails loudly at startup rather than silently charging via
      the mock.
    """
    if settings.payment_gateway == "razorpay":
        if not settings.razorpay_configured:
            raise RuntimeError(
                "PAYMENT_GATEWAY=razorpay but Razorpay credentials are incomplete; "
                "set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET"
            )
        # Lazy import: keeps the razorpay SDK out of the default (mock) path.
        from app.services.razorpay_gateway import RazorpayGateway

        return RazorpayGateway(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
            webhook_secret=settings.razorpay_webhook_secret,
        )
    return default_gateway


@lru_cache
def _cached_gateway() -> PaymentGateway:
    return build_gateway(get_settings())


def get_gateway() -> PaymentGateway:
    """FastAPI dependency / service entrypoint returning the live gateway.

    Cached per process (the gateway is stateless with respect to the DB session,
    which is passed per call). Tests select Razorpay mode by overriding this
    dependency with a gateway wired to a fake client — no network, no creds.
    """
    return _cached_gateway()
