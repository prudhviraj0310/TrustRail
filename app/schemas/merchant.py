"""Merchant API schemas (Phase 4)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProductOut(BaseModel):
    sku: str
    name: str
    price: int  # minor units
    currency: str
    inventory: int
    delivery_info: dict
    policy: dict


class InventoryOut(BaseModel):
    sku: str
    available: int
    in_stock: bool


class CheckoutItemIn(BaseModel):
    sku: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class CheckoutValidateIn(BaseModel):
    items: list[CheckoutItemIn] = Field(min_length=1)


class CheckoutLineOut(BaseModel):
    sku: str
    name: str | None
    unit_price: int | None
    quantity: int
    line_total: int | None
    currency: str | None
    available: int
    in_stock: bool
    known: bool


class CheckoutValidateOut(BaseModel):
    currency: str | None
    total: int
    all_available: bool
    all_known: bool
    unknown_skus: list[str]
    currency_conflict: bool
    lines: list[CheckoutLineOut]


class OrderCreateIn(BaseModel):
    items: list[CheckoutItemIn] = Field(min_length=1)
    idempotency_key: str | None = None


class OrderOut(BaseModel):
    order_id: str
    status: str
    items: list
    total: int
    currency: str
    created_at: datetime
