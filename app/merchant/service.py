"""Merchant business logic (operates directly on a SQLAlchemy Session).

This is the authoritative merchant-side behaviour: pricing, stock, order
creation with idempotency, and cancellation. TrustRail never imports this module
directly — it goes through :class:`app.merchant.client.MerchantClient`.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import (
    InsufficientInventory,
    MerchantOrderFailed,
    OrderNotFound,
    ProductNotFound,
)
from app.ids import new_order_id
from app.models.merchant import MerchantOrder, MerchantProduct
from app.schemas.merchant import CheckoutLineOut, CheckoutValidateOut


def list_products(db: Session) -> Sequence[MerchantProduct]:
    return db.scalars(select(MerchantProduct).order_by(MerchantProduct.sku)).all()


def get_product(db: Session, sku: str) -> MerchantProduct | None:
    return db.get(MerchantProduct, sku)


def get_inventory(db: Session, sku: str) -> int | None:
    product = db.get(MerchantProduct, sku)
    return None if product is None else product.inventory


def _normalise_items(items: Sequence) -> list[tuple[str, int]]:
    """Accept pydantic items or dicts; return [(sku, quantity), ...]."""
    out: list[tuple[str, int]] = []
    for it in items:
        if isinstance(it, dict):
            out.append((str(it["sku"]), int(it["quantity"])))
        else:
            out.append((str(it.sku), int(it.quantity)))
    return out


def checkout_validate(db: Session, items: Sequence) -> CheckoutValidateOut:
    """Price a basket and report availability without mutating anything."""
    normalised = _normalise_items(items)
    lines: list[CheckoutLineOut] = []
    unknown: list[str] = []
    currencies: set[str] = set()
    total = 0
    all_available = True

    for sku, qty in normalised:
        product = db.get(MerchantProduct, sku)
        if product is None:
            unknown.append(sku)
            all_available = False
            lines.append(
                CheckoutLineOut(
                    sku=sku,
                    name=None,
                    unit_price=None,
                    quantity=qty,
                    line_total=None,
                    currency=None,
                    available=0,
                    in_stock=False,
                    known=False,
                )
            )
            continue

        line_total = product.price * qty
        in_stock = product.inventory >= qty
        if not in_stock:
            all_available = False
        currencies.add(product.currency)
        total += line_total
        lines.append(
            CheckoutLineOut(
                sku=sku,
                name=product.name,
                unit_price=product.price,
                quantity=qty,
                line_total=line_total,
                currency=product.currency,
                available=product.inventory,
                in_stock=in_stock,
                known=True,
            )
        )

    currency_conflict = len(currencies) > 1
    currency = next(iter(currencies)) if len(currencies) == 1 else None

    # Check for bundle / cross-sell discount
    from app.merchant.growth import MERCHANT_BUNDLES

    item_map = dict(normalised)
    for b in MERCHANT_BUNDLES:
        bundle_map = {it["sku"]: it.get("quantity", 1) for it in b["skus"]}
        if all(item_map.get(bsku, 0) >= bqty for bsku, bqty in bundle_map.items()):
            # Bundle match: adjust total to bundle price
            bundle_orig = sum(
                db.get(MerchantProduct, bsku).price * bqty
                for bsku, bqty in bundle_map.items()
            )
            discount = bundle_orig - b["bundle_price"]
            total = max(0, total - discount)
            break

    return CheckoutValidateOut(
        currency=currency,
        total=total,
        all_available=all_available,
        all_known=not unknown,
        unknown_skus=unknown,
        currency_conflict=currency_conflict,
        lines=lines,
    )


def create_order(
    db: Session, items: Sequence, idempotency_key: str | None = None
) -> MerchantOrder:
    """Create an order, decrementing stock. Idempotent on ``idempotency_key``.

    Raises ProductNotFound / InsufficientInventory / MerchantOrderFailed.
    """
    # Idempotency: a repeated create with the same key returns the same order.
    if idempotency_key:
        existing = db.scalar(
            select(MerchantOrder).where(MerchantOrder.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing

    normalised = _normalise_items(items)
    total = 0
    currency: str | None = None
    resolved: list[tuple[MerchantProduct, int, int]] = []

    for sku, qty in normalised:
        product = db.get(MerchantProduct, sku)
        if product is None:
            raise ProductNotFound(sku)
        if product.inventory < qty:
            raise InsufficientInventory(sku, qty, product.inventory)
        if product.force_order_failure:
            # Deterministic fulfilment failure (payment may already have happened).
            raise MerchantOrderFailed(f"Merchant failed to fulfil {sku}")
        line_total = product.price * qty
        total += line_total
        currency = product.currency
        resolved.append((product, qty, line_total))

    # Check for bundle / cross-sell discount
    from app.merchant.growth import MERCHANT_BUNDLES

    item_map = dict(normalised)
    for b in MERCHANT_BUNDLES:
        bundle_map = {it["sku"]: it.get("quantity", 1) for it in b["skus"]}
        if all(item_map.get(bsku, 0) >= bqty for bsku, bqty in bundle_map.items()):
            bundle_orig = sum(
                db.get(MerchantProduct, bsku).price * bqty
                for bsku, bqty in bundle_map.items()
            )
            discount = bundle_orig - b["bundle_price"]
            total = max(0, total - discount)
            break

    # All checks passed — commit the stock decrement and the order atomically.
    for product, qty, _ in resolved:
        product.inventory -= qty

    order = MerchantOrder(
        id=new_order_id(),
        idempotency_key=idempotency_key,
        items=[{"sku": p.sku, "quantity": q, "line_total": lt} for p, q, lt in resolved],
        total=total,
        currency=currency or "INR",
        status="CONFIRMED",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, order_id: str) -> MerchantOrder | None:
    return db.get(MerchantOrder, order_id)


def cancel_order(db: Session, order_id: str) -> MerchantOrder:
    order = db.get(MerchantOrder, order_id)
    if order is None:
        raise OrderNotFound(order_id)
    if order.status == "CANCELLED":
        return order
    # Restore stock on cancellation.
    for line in order.items:
        product = db.get(MerchantProduct, line["sku"])
        if product is not None:
            product.inventory += int(line["quantity"])
    order.status = "CANCELLED"
    db.commit()
    db.refresh(order)
    return order
