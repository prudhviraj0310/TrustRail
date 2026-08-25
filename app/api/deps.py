"""Shared API dependencies and mappers."""

from __future__ import annotations

from app.models.transaction import Transaction
from app.schemas.transaction import DecisionEnvelopeOut, TransactionOut
from app.services.policy import PolicyResult


def transaction_out(txn: Transaction) -> TransactionOut:
    return TransactionOut(
        transaction_id=txn.id,
        transaction_identity=txn.transaction_identity,
        state=txn.state,
        merchant_id=txn.merchant_id,
        currency=txn.currency,
        authorized_max_amount=txn.authorized_max_amount,
        quoted_total=txn.quoted_total,
        max_quantity=txn.max_quantity,
        authorized_expires_at=txn.authorized_expires_at,
        payment_ref=txn.payment_ref,
        merchant_order_id=txn.merchant_order_id,
        amount_captured=txn.amount_captured,
        payment_provider=txn.payment_provider,
        payment_status=txn.payment_status,
        razorpay_order_id=txn.razorpay_order_id,
        razorpay_payment_id=txn.razorpay_payment_id,
        razorpay_refund_id=txn.razorpay_refund_id,
        created_at=txn.created_at,
        updated_at=txn.updated_at,
    )


def decision_envelope(
    intent_id: str | None, txn: Transaction, result: PolicyResult
) -> DecisionEnvelopeOut:
    return DecisionEnvelopeOut(
        intent_id=intent_id,
        transaction_id=txn.id,
        transaction_identity=txn.transaction_identity,
        state=txn.state,
        decision=result.as_schema(),
    )
