"""Razorpay webhook endpoint (Phase 2 / STEP 8).

``POST /webhooks/razorpay`` is the asynchronous ingress for payment outcomes.
Security properties enforced here:

* The signature is verified over the **raw request body** (not a re-serialised
  copy) using the server-side webhook secret, before the body is parsed or
  trusted. An invalid or missing signature is rejected with 400 and no state
  change occurs.
* The endpoint only functions when a real Razorpay gateway is configured; in the
  default mock mode it returns 503 rather than pretending to accept events.
* The AI buyer plays no part here: this route is called by Razorpay's servers,
  and nothing it delivers can set state except through the authoritative,
  legality-checked processing in :mod:`app.services.webhook`.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.clock import Clock, get_clock
from app.db import get_db
from app.services import webhook as webhook_service
from app.services.gateway import get_gateway
from app.services.payment import PaymentGateway

router = APIRouter(prefix="/webhooks", tags=["trustrail: webhooks"])


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    gateway: PaymentGateway = Depends(get_gateway),
) -> JSONResponse:
    # Webhooks are meaningful only with a real gateway. The mock has no secret to
    # verify against, so refuse rather than accept unverifiable events.
    verify = getattr(gateway, "verify_webhook_signature", None)
    if verify is None:
        return JSONResponse(
            status_code=503,
            content={"error": "razorpay webhooks are not enabled"},
        )

    raw_body = await request.body()

    if not x_razorpay_signature or not verify(raw_body, x_razorpay_signature):
        # Authenticity failure — do not parse or act on the payload.
        return JSONResponse(
            status_code=400,
            content={"error": "invalid webhook signature"},
        )

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400, content={"error": "malformed webhook body"}
        )

    outcome = webhook_service.process_razorpay_event(db, event, clock=clock)
    # 200 for anything we accepted-and-recorded so Razorpay stops retrying.
    return JSONResponse(status_code=200, content=outcome)
