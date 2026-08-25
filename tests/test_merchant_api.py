"""Phase 4 — merchant mock API tests.

The merchant is a *separate* system from TrustRail. These tests hit it directly
under /merchant to confirm pricing, stock, idempotent orders, and cancellation.
"""

from __future__ import annotations

from app.merchant.catalogue import CATALOGUE


def test_list_products_returns_full_catalogue(client):
    r = client.get("/merchant/products")
    assert r.status_code == 200
    assert len(r.json()) == len(CATALOGUE)


def test_get_known_product(client):
    r = client.get("/merchant/products/SKU-001")
    assert r.status_code == 200
    body = r.json()
    assert body["price"] == 129900
    assert body["currency"] == "INR"


def test_get_unknown_product_404(client):
    r = client.get("/merchant/products/SKU-NOPE")
    assert r.status_code == 404


def test_inventory_in_stock(client):
    r = client.get("/merchant/inventory/SKU-001")
    assert r.status_code == 200
    assert r.json() == {"sku": "SKU-001", "available": 50, "in_stock": True}


def test_inventory_out_of_stock(client):
    r = client.get("/merchant/inventory/SKU-OOS")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] == 0
    assert body["in_stock"] is False


def test_checkout_validate_prices_basket(client):
    r = client.post(
        "/merchant/checkout/validate",
        json={"items": [{"sku": "SKU-001", "quantity": 2}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 259800  # 2 x 129900
    assert body["all_available"] is True
    assert body["all_known"] is True
    assert body["currency"] == "INR"


def test_checkout_validate_flags_unknown_sku(client):
    r = client.post(
        "/merchant/checkout/validate",
        json={"items": [{"sku": "SKU-GHOST", "quantity": 1}]},
    )
    body = r.json()
    assert body["all_known"] is False
    assert "SKU-GHOST" in body["unknown_skus"]


def test_checkout_validate_flags_currency_conflict(client):
    r = client.post(
        "/merchant/checkout/validate",
        json={"items": [{"sku": "SKU-001", "quantity": 1},
                        {"sku": "SKU-USD", "quantity": 1}]},
    )
    body = r.json()
    assert body["currency_conflict"] is True
    assert body["currency"] is None


def test_create_order_decrements_stock(client):
    before = client.get("/merchant/inventory/SKU-001").json()["available"]
    r = client.post(
        "/merchant/orders",
        json={"items": [{"sku": "SKU-001", "quantity": 2}], "idempotency_key": "key-abc"},
    )
    assert r.status_code == 201
    order = r.json()
    assert order["status"] == "CONFIRMED"
    assert order["total"] == 259800
    after = client.get("/merchant/inventory/SKU-001").json()["available"]
    assert after == before - 2


def test_create_order_is_idempotent(client):
    body = {"items": [{"sku": "SKU-003", "quantity": 1}], "idempotency_key": "key-idem"}
    first = client.post("/merchant/orders", json=body).json()
    before_second = client.get("/merchant/inventory/SKU-003").json()["available"]
    second = client.post("/merchant/orders", json=body).json()
    after_second = client.get("/merchant/inventory/SKU-003").json()["available"]

    assert first["order_id"] == second["order_id"]
    assert before_second == after_second  # no second decrement


def test_create_order_insufficient_inventory_409(client):
    r = client.post(
        "/merchant/orders", json={"items": [{"sku": "SKU-OOS", "quantity": 1}]}
    )
    assert r.status_code == 409


def test_create_order_unknown_sku_404(client):
    r = client.post(
        "/merchant/orders", json={"items": [{"sku": "SKU-GHOST", "quantity": 1}]}
    )
    assert r.status_code == 404


def test_create_order_forced_fulfilment_failure_502(client):
    r = client.post(
        "/merchant/orders", json={"items": [{"sku": "SKU-FAIL-ORDER", "quantity": 1}]}
    )
    assert r.status_code == 502


def test_get_order_roundtrip(client):
    created = client.post(
        "/merchant/orders", json={"items": [{"sku": "SKU-002", "quantity": 1}]}
    ).json()
    r = client.get(f"/merchant/orders/{created['order_id']}")
    assert r.status_code == 200
    assert r.json()["order_id"] == created["order_id"]


def test_cancel_order_restores_stock(client):
    before = client.get("/merchant/inventory/SKU-002").json()["available"]
    created = client.post(
        "/merchant/orders", json={"items": [{"sku": "SKU-002", "quantity": 1}]}
    ).json()
    assert client.get("/merchant/inventory/SKU-002").json()["available"] == before - 1

    r = client.post(f"/merchant/orders/{created['order_id']}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    assert client.get("/merchant/inventory/SKU-002").json()["available"] == before


def test_cancel_unknown_order_404(client):
    r = client.post("/merchant/orders/ord_does_not_exist/cancel")
    assert r.status_code == 404
