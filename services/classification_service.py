"""Service-layer functions for classifying transactions.

Public API: apply_classification, manual_override, resolve_active_classification.
Internal (admin/seed only — not part of the public service contract): create_rule.

Every write function records a paired AuditEvent and leaves the caller responsible
for session.commit(). resolve_active_classification delegates precedence resolution
to domain.classification.resolve_active and may return a synthetic
_TransferClassification (not an ORM row) when a TransferLink exists.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import or_
from sqlalchemy.orm import Session

import domain.classification as domain_cls
from storage.models import AuditEvent, Classification, ClassificationRule, TransferLink

# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def apply_classification(
    session: Session,
    transaction_id: str,
    source: str,
    transaction_type: str,
    category_id: str | None,
    merchant: str | None,
    rationale: str | None,
) -> Classification:
    """Insert a Classification row and a paired AuditEvent, then return the row.

    Raises ValueError for an unrecognised source or transaction_type.
    Does not call session.commit() — the caller owns the transaction boundary.
    """
    if source not in Classification.SOURCES:
        raise ValueError(
            f"Unknown classification source {source!r}. Valid values: {Classification.SOURCES}"
        )
    if transaction_type not in Classification.TRANSACTION_TYPES:
        raise ValueError(
            f"Unknown transaction_type {transaction_type!r}. Valid values: {Classification.TRANSACTION_TYPES}"
        )

    classification = Classification(
        transaction_id=transaction_id,
        source=source,
        transaction_type=transaction_type,
        category_id=category_id,
        merchant=merchant,
        rationale=rationale,
        review_state="needs_review",
    )
    session.add(classification)
    session.flush()  # populate classification.id for the audit record

    audit = AuditEvent(
        entity_type="classification",
        entity_id=classification.id,
        action="classified",
        actor=source,
        before_state=None,
        after_state={
            "transaction_id": transaction_id,
            "source": source,
            "transaction_type": transaction_type,
            "category_id": category_id,
            "merchant": merchant,
            "review_state": classification.review_state,
        },
    )
    session.add(audit)

    return classification


def manual_override(
    session: Session,
    transaction_id: str,
    transaction_type: str,
    category_id: str | None,
    merchant: str | None,
) -> Classification:
    """Apply a human-confirmed classification and mark it immediately accepted.

    Delegates to apply_classification with source=manual, then sets
    review_state=accepted and writes an audit event with actor=manual.
    Does not call session.commit() — the caller owns the transaction boundary.
    """
    classification = apply_classification(
        session=session,
        transaction_id=transaction_id,
        source="manual",
        transaction_type=transaction_type,
        category_id=category_id,
        merchant=merchant,
        rationale=None,
    )
    classification.review_state = "accepted"

    audit = AuditEvent(
        entity_type="classification",
        entity_id=classification.id,
        action="manual_override",
        actor="manual",
        before_state=None,
        after_state={
            "transaction_id": transaction_id,
            "transaction_type": transaction_type,
            "category_id": category_id,
            "merchant": merchant,
            "review_state": "accepted",
        },
    )
    session.add(audit)

    return classification


def resolve_active_classification(
    session: Session,
    transaction_id: str,
) -> domain_cls.Classification | None:
    """Return the single active classification for a transaction, or None.

    Loads all Classification rows and checks for a confirmed TransferLink, then
    delegates to domain.classification.resolve_active for precedence resolution.
    May return a synthetic _TransferClassification instance (not an ORM row) when
    a transfer link is confirmed but no internal_transfer Classification row exists.
    """
    orm_rows = (
        session.query(Classification)
        .filter(Classification.transaction_id == transaction_id)
        .all()
    )
    # Cast satisfies list invariance: ORM Classification rows structurally implement
    # the domain Classification Protocol but Pyright cannot verify this via invariant list.
    classifications = cast(list[domain_cls.Classification], orm_rows)

    transfer_link = (
        session.query(TransferLink)
        .filter(
            or_(
                TransferLink.transaction_a_id == transaction_id,
                TransferLink.transaction_b_id == transaction_id,
            )
        )
        .first()
    )
    has_transfer_link = transfer_link is not None

    return domain_cls.resolve_active(classifications, has_transfer_link)


# ---------------------------------------------------------------------------
# Internal helpers (not part of the public service contract)
# ---------------------------------------------------------------------------


def create_rule(
    session: Session,
    pattern_type: str,
    pattern_value: str,
    transaction_type: str,
    category_id: str | None = None,
    merchant: str | None = None,
    priority: int = 0,
) -> ClassificationRule:
    """Insert a confirmed ClassificationRule and return it.

    Internal — callers outside this module should not depend on this function
    directly; use higher-level service operations instead.

    Raises ValueError for an unrecognised pattern_type or transaction_type.
    Does not call session.commit() — the caller owns the transaction boundary.
    """
    if pattern_type not in ClassificationRule.PATTERN_TYPES:
        raise ValueError(
            f"Unknown pattern_type {pattern_type!r}. Valid values: {ClassificationRule.PATTERN_TYPES}"
        )
    if transaction_type not in Classification.TRANSACTION_TYPES:
        raise ValueError(
            f"Unknown transaction_type {transaction_type!r}. Valid values: {Classification.TRANSACTION_TYPES}"
        )

    rule = ClassificationRule(
        priority=priority,
        pattern_type=pattern_type,
        pattern_value=pattern_value,
        transaction_type=transaction_type,
        category_id=category_id,
        merchant=merchant,
        is_confirmed=True,
    )
    session.add(rule)
    return rule
