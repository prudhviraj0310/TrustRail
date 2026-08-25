"""Domain exceptions.

These map to HTTP errors at the API boundary (see app/api). Keeping them
separate from FastAPI keeps the services framework-agnostic and unit-testable.
"""

from __future__ import annotations


class TrustRailError(Exception):
    """Base class for all TrustRail domain errors."""


class InvalidStateTransition(TrustRailError):
    """Raised when code attempts a transition the state machine forbids."""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Illegal transaction state transition: {from_state} -> {to_state}"
        )


class IntentNotFound(TrustRailError):
    def __init__(self, intent_id: str) -> None:
        self.intent_id = intent_id
        super().__init__(f"Intent not found: {intent_id}")


class TransactionNotFound(TrustRailError):
    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(f"Transaction not found: {ref}")


class InvalidLifecycleState(TrustRailError):
    """Raised when an operation is attempted from the wrong lifecycle state."""


class ProductNotFound(TrustRailError):
    def __init__(self, sku: str) -> None:
        self.sku = sku
        super().__init__(f"Product not found: {sku}")


class OrderNotFound(TrustRailError):
    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        super().__init__(f"Order not found: {order_id}")


class InsufficientInventory(TrustRailError):
    def __init__(self, sku: str, requested: int, available: int) -> None:
        self.sku = sku
        self.requested = requested
        self.available = available
        super().__init__(
            f"Insufficient inventory for {sku}: requested {requested}, available {available}"
        )


class MerchantOrderFailed(TrustRailError):
    """The merchant refused/failed to create the order (simulated fulfilment failure)."""
