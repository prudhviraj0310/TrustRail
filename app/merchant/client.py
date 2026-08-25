"""The MerchantClient seam.

TrustRail coordinates with the merchant *only* through this interface. In Phase 1
the implementation is in-process (direct DB calls); in Phase 2 an HTTP-backed
implementation could be dropped in without touching the orchestration code. The
method surface deliberately mirrors a network client (no DB session leaks into
the signatures — the in-process client is constructed bound to one session).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.orm import Session

from app.merchant import service
from app.models.merchant import MerchantOrder, MerchantProduct
from app.schemas.merchant import CheckoutValidateOut


class MerchantClient(Protocol):
    def get_product(self, sku: str) -> MerchantProduct | None: ...
    def get_inventory(self, sku: str) -> int | None: ...
    def checkout_validate(self, items: Sequence) -> CheckoutValidateOut: ...
    def create_order(
        self, items: Sequence, idempotency_key: str | None = None
    ) -> MerchantOrder: ...
    def get_order(self, order_id: str) -> MerchantOrder | None: ...
    def cancel_order(self, order_id: str) -> MerchantOrder: ...


class InProcessMerchantClient:
    """In-process MerchantClient bound to a single database session."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_product(self, sku: str) -> MerchantProduct | None:
        return service.get_product(self._db, sku)

    def get_inventory(self, sku: str) -> int | None:
        return service.get_inventory(self._db, sku)

    def checkout_validate(self, items: Sequence) -> CheckoutValidateOut:
        return service.checkout_validate(self._db, items)

    def create_order(
        self, items: Sequence, idempotency_key: str | None = None
    ) -> MerchantOrder:
        return service.create_order(self._db, items, idempotency_key)

    def get_order(self, order_id: str) -> MerchantOrder | None:
        return service.get_order(self._db, order_id)

    def cancel_order(self, order_id: str) -> MerchantOrder:
        return service.cancel_order(self._db, order_id)
