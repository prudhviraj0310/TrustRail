"""Synthetic merchant catalogue + idempotent seeding.

Prices are integer minor units (paise). A few deliberately-crafted SKUs make the
failure/recovery states demonstrable and testable:

* ``SKU-OOS``        — zero inventory (INVENTORY_CHANGED / block)
* ``SKU-USD``        — priced in USD (currency-mismatch block)
* ``SKU-FAIL-PAY``   — the mock gateway declines it (PAYMENT_FAILED)
* ``SKU-FAIL-ORDER`` — the merchant fails fulfilment (ORDER_FAILED -> REFUND_REQUIRED)
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.merchant import MerchantProduct

CATALOGUE: list[dict] = [
    {
        "sku": "SKU-001",
        "name": "Wireless Mouse",
        "price": 129900,  # ₹1,299.00
        "currency": "INR",
        "inventory": 50,
        "delivery_info": {"eta_days": 3, "ships_to": ["IN"]},
        "policy": {"max_per_order": 5, "returnable": True},
    },
    {
        "sku": "SKU-002",
        "name": "Mechanical Keyboard",
        "price": 499900,  # ₹4,999.00
        "currency": "INR",
        "inventory": 20,
        "delivery_info": {"eta_days": 4, "ships_to": ["IN"]},
        "policy": {"max_per_order": 3, "returnable": True},
    },
    {
        "sku": "SKU-003",
        "name": "USB-C Hub",
        "price": 249900,  # ₹2,499.00
        "currency": "INR",
        "inventory": 35,
        "delivery_info": {"eta_days": 2, "ships_to": ["IN"]},
        "policy": {"max_per_order": 10, "returnable": True},
    },
    {
        "sku": "SKU-004",
        "name": "27-inch 4K Monitor",
        "price": 1899900,  # ₹18,999.00
        "currency": "INR",
        "inventory": 8,
        "delivery_info": {"eta_days": 6, "ships_to": ["IN"]},
        "policy": {"max_per_order": 2, "returnable": False},
    },
    {
        "sku": "SKU-OOS",
        "name": "Limited Edition Dock (sold out)",
        "price": 199900,
        "currency": "INR",
        "inventory": 0,
        "delivery_info": {"eta_days": 7, "ships_to": ["IN"]},
        "policy": {"max_per_order": 1, "returnable": False},
    },
    {
        "sku": "SKU-USD",
        "name": "Imported Gadget (USD priced)",
        "price": 9900,  # $99.00
        "currency": "USD",
        "inventory": 15,
        "delivery_info": {"eta_days": 12, "ships_to": ["IN", "US"]},
        "policy": {"max_per_order": 2, "returnable": True},
    },
    {
        "sku": "SKU-FAIL-PAY",
        "name": "Gateway-Decline Test Item",
        "price": 100000,  # ₹1,000.00
        "currency": "INR",
        "inventory": 100,
        "delivery_info": {"eta_days": 3, "ships_to": ["IN"]},
        "policy": {"max_per_order": 10, "returnable": True},
        "force_payment_decline": True,
    },
    {
        "sku": "SKU-FAIL-ORDER",
        "name": "Fulfilment-Failure Test Item",
        "price": 100000,  # ₹1,000.00
        "currency": "INR",
        "inventory": 100,
        "delivery_info": {"eta_days": 3, "ships_to": ["IN"]},
        "policy": {"max_per_order": 10, "returnable": True},
        "force_order_failure": True,
    },
]

MERCHANT_ID = "MERCH_DEMO_001"


def seed_merchant(db: Session, *, reset: bool = False) -> int:
    """Idempotently seed the catalogue. Returns the number of products written."""
    if reset:
        db.query(MerchantProduct).delete()
        db.commit()

    written = 0
    for row in CATALOGUE:
        existing = db.get(MerchantProduct, row["sku"])
        if existing is not None:
            continue
        db.add(
            MerchantProduct(
                sku=row["sku"],
                name=row["name"],
                price=row["price"],
                currency=row["currency"],
                inventory=row["inventory"],
                delivery_info=row["delivery_info"],
                policy=row["policy"],
                force_payment_decline=row.get("force_payment_decline", False),
                force_order_failure=row.get("force_order_failure", False),
            )
        )
        written += 1
    if written:
        db.commit()
    return written
