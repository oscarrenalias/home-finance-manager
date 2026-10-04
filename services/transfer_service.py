"""Service-layer functions for linking and unlinking internal transfer pairs.

Public API: find_transfer_candidates, confirm_transfer, undo_transfer.

Transfer linking semantics:
- A valid transfer pair has opposite amount_cents, belongs to different accounts,
  and falls within a ±3 calendar-day window. Neither side may already be linked.
- confirm_transfer creates a TransferLink and writes an internal_transfer
  Classification (source=rule) for both transactions. TransferLink IDs are stored
  in lexicographic order to satisfy the DB check constraint.
- undo_transfer removes the TransferLink but does NOT delete the Classification rows
  that were created at link time. domain.classification.resolve_active re-evaluates
  precedence; without the link, those rows may be superseded by a later manual
  override or simply become stale entries with no downstream effect.
- All write functions leave session.commit() to the caller.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.classification_service import apply_classification
from storage.models import AuditEvent, Transaction, TransferLink


def find_transfer_candidates(session: Session, transaction_id: str) -> list[Transaction]:
    """Return transactions that are candidates to be the other side of a transfer.

    Candidates must:
    - belong to a different account than the source transaction
    - have amount_cents equal to the negation of the source amount
    - fall within ±3 calendar days of the source date
    - not already be linked via a TransferLink (as either side)
    """
    source = session.get(Transaction, transaction_id)
    if source is None:
        return []

    window_start = source.date - timedelta(days=3)
    window_end = source.date + timedelta(days=3)
    opposite_amount = -source.amount_cents

    # Collect transaction IDs that are already part of any transfer link.
    linked_ids_stmt = select(TransferLink.transaction_a_id).union(
        select(TransferLink.transaction_b_id)
    )
    linked_ids = {row[0] for row in session.execute(linked_ids_stmt)}
    # Always exclude the source transaction itself.
    linked_ids.add(transaction_id)

    candidates = (
        session.query(Transaction)
        .filter(
            Transaction.account_id != source.account_id,
            Transaction.amount_cents == opposite_amount,
            Transaction.date >= window_start,
            Transaction.date <= window_end,
            Transaction.id.not_in(linked_ids),
        )
        .all()
    )
    return candidates


def confirm_transfer(
    session: Session,
    transaction_a_id: str,
    transaction_b_id: str,
) -> TransferLink:
    """Create a confirmed TransferLink for two transactions and classify both.

    IDs are ordered lexicographically before insert to satisfy the model's
    CheckConstraint. Both transactions receive an internal_transfer Classification
    (source=rule) and a transfer_linked AuditEvent.

    Does not call session.commit() — the caller owns the transaction boundary.
    """
    # Enforce lexicographic ordering required by the DB check constraint.
    if transaction_a_id > transaction_b_id:
        transaction_a_id, transaction_b_id = transaction_b_id, transaction_a_id

    link = TransferLink(
        transaction_a_id=transaction_a_id,
        transaction_b_id=transaction_b_id,
        confirmed_by="manual",
    )
    session.add(link)
    session.flush()  # populate link.id before writing audit events

    for txn_id in (transaction_a_id, transaction_b_id):
        apply_classification(
            session=session,
            transaction_id=txn_id,
            source="rule",
            transaction_type="internal_transfer",
            category_id=None,
            merchant=None,
            rationale="Linked as internal transfer",
        )
        audit = AuditEvent(
            entity_type="transaction",
            entity_id=txn_id,
            action="transfer_linked",
            actor="manual",
            before_state=None,
            after_state={"transfer_link_id": link.id},
        )
        session.add(audit)

    return link


def undo_transfer(session: Session, link_id: str) -> None:
    """Remove a TransferLink and record audit events for both transactions.

    Idempotent: if the link does not exist, returns without error.
    Does not delete Classification rows created when the link was confirmed.
    Does not call session.commit() — the caller owns the transaction boundary.
    """
    link = session.get(TransferLink, link_id)
    if link is None:
        return

    transaction_a_id = link.transaction_a_id
    transaction_b_id = link.transaction_b_id

    session.delete(link)
    session.flush()

    for txn_id in (transaction_a_id, transaction_b_id):
        audit = AuditEvent(
            entity_type="transaction",
            entity_id=txn_id,
            action="transfer_unlinked",
            actor="manual",
            before_state={"transfer_link_id": link_id},
            after_state=None,
        )
        session.add(audit)
