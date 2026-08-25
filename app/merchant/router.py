"""Merchant mock API (Phase 4).

Exposes the external merchant endpoints TrustRail coordinates with. Mounted under
``/merchant`` by the main app. This is intentionally a *separate* system from
TrustRail's own API.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import InsufficientInventory, MerchantOrderFailed, ProductNotFound
from app.merchant import service
from app.merchant.catalogue import MERCHANT_ID
from app.models.merchant import MerchantOrder, MerchantProduct
from app.schemas.merchant import (
    AgentCommerceManifestOut,
    CheckoutValidateIn,
    CheckoutValidateOut,
    InventoryOut,
    OrderCreateIn,
    OrderOut,
    ProductOut,
)

router = APIRouter(prefix="/merchant", tags=["merchant (mock external system)"])


def _product_out(p: MerchantProduct) -> ProductOut:
    return ProductOut(
        sku=p.sku,
        name=p.name,
        price=p.price,
        currency=p.currency,
        inventory=p.inventory,
        delivery_info=p.delivery_info,
        policy=p.policy,
    )


def _order_out(o: MerchantOrder) -> OrderOut:
    return OrderOut(
        order_id=o.id,
        status=o.status,
        items=o.items,
        total=o.total,
        currency=o.currency,
        created_at=o.created_at,
    )


@router.get("/agent-card", response_model=AgentCommerceManifestOut)
def agent_card(db: Session = Depends(get_db)) -> AgentCommerceManifestOut:
    """Expose a machine-readable catalogue and bounded purchase workflow.

    The card is read-only: it helps an AI buyer discover what the merchant sells
    and how to make a structured proposal. TrustRail still owns policy, state,
    payment confirmation, and the audit trail.
    """
    return AgentCommerceManifestOut(
        schema_version="trustrail-agent-commerce/v1",
        merchant_id=MERCHANT_ID,
        merchant_name="TrustRail Demo Merchant",
        currency="INR",
        money_unit="minor",
        catalogue_endpoint="/merchant/products",
        checkout_validation_endpoint="/merchant/checkout/validate",
        purchase_intent_endpoint="/intents",
        transaction_execution_endpoint="/transactions",
        transaction_lookup_endpoint_template="/transactions/{transaction_id}",
        audit_endpoint_template="/transactions/{transaction_id}/audit",
        required_purchase_intent_fields=[
            "agent_id",
            "merchant_id",
            "items[].sku",
            "items[].quantity",
            "constraints.max_amount",
            "constraints.currency",
            "constraints.max_quantity",
            "authorization.expires_at",
        ],
        agent_can=[
            "discover products and inventory",
            "request a checkout quote",
            "propose a structured PurchaseIntent",
            "read the resulting transaction and audit trail",
        ],
        trustrail_controls=[
            "canonicalizes financially relevant intent fields",
            "evaluates deterministic merchant and authorization policy",
            "enforces legal transaction state transitions",
            "confirms payment only from gateway evidence",
            "records an append-only audit trail",
        ],
        products=[_product_out(product) for product in service.list_products(db)],
    )


@router.get("/products", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db)) -> list[ProductOut]:
    return [_product_out(p) for p in service.list_products(db)]


@router.get("/products/{sku}", response_model=ProductOut)
def get_product(sku: str, db: Session = Depends(get_db)) -> ProductOut:
    product = service.get_product(db, sku)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown SKU: {sku}")
    return _product_out(product)


@router.get("/inventory/{sku}", response_model=InventoryOut)
def get_inventory(sku: str, db: Session = Depends(get_db)) -> InventoryOut:
    available = service.get_inventory(db, sku)
    if available is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown SKU: {sku}")
    return InventoryOut(sku=sku, available=available, in_stock=available > 0)


@router.post("/checkout/validate", response_model=CheckoutValidateOut)
def checkout_validate(
    payload: CheckoutValidateIn, db: Session = Depends(get_db)
) -> CheckoutValidateOut:
    return service.checkout_validate(db, payload.items)


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreateIn, db: Session = Depends(get_db)) -> OrderOut:
    try:
        order = service.create_order(db, payload.items, payload.idempotency_key)
    except ProductNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InsufficientInventory as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except MerchantOrderFailed as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return _order_out(order)


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: str, db: Session = Depends(get_db)) -> OrderOut:
    order = service.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown order: {order_id}")
    return _order_out(order)


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: str, db: Session = Depends(get_db)) -> OrderOut:
    from app.errors import OrderNotFound

    try:
        order = service.cancel_order(db, order_id)
    except OrderNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _order_out(order)
