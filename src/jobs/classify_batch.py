"""Classify-batch job handler — classifies all unclassified transactions in a batch.

Idempotency (A11): transactions with an existing accepted Classification are skipped at
the DB query level, so re-running or retrying this job never overwrites a manual override
or a previously auto-accepted result.

Commit strategy: results are written and committed after each chunk of CHUNK_SIZE
transactions so progress is visible immediately and partial work is preserved if the
worker crashes mid-batch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from config.categories import load_categories
from llm.classifier import AbstractClassifier, ClassificationRequest
from services.classification_service import apply_classification
from storage.models import AuditEvent, Classification, SourceObservation, Transaction

if TYPE_CHECKING:
    from storage.models import Job

logger = logging.getLogger(__name__)

AUTO_ACCEPT_THRESHOLD = 0.80
CHUNK_SIZE = 50

_ALWAYS_REVIEW_TYPES = frozenset({"internal_transfer", "external_transfer", "contribution", "unknown"})


def handle_classify_batch(
    session: Session,
    job: "Job",
    classifier: AbstractClassifier,
) -> None:
    """Classify all unclassified transactions in the given batch.

    Inputs schema: {"batch_id": "<uuid>"}
    Skips transactions with an existing accepted Classification (A11 idempotency).
    Commits after each chunk of CHUNK_SIZE — progress is visible progressively.
    """
    if not job.inputs:
        raise ValueError(f"Job {job.id} has no inputs; expected {{batch_id: <uuid>}}")
    batch_id: str = job.inputs["batch_id"]

    categories = load_categories()
    category_dicts = [
        {"id": c.id, "name": c.name, "guidance": c.guidance, "examples": c.examples}
        for c in categories
    ]

    accepted_ids_subq = (
        select(Classification.transaction_id)
        .where(Classification.review_state == "accepted")
        .scalar_subquery()
    )

    transactions = (
        session.query(Transaction)
        .options(joinedload(Transaction.account))
        .join(SourceObservation, SourceObservation.transaction_id == Transaction.id)
        .where(
            SourceObservation.batch_id == batch_id,
            ~Transaction.id.in_(accepted_ids_subq),
        )
        .all()
    )

    logger.info(
        "classify_batch: batch=%s eligible_transactions=%d chunk_size=%d",
        batch_id, len(transactions), CHUNK_SIZE,
    )

    for chunk_start in range(0, len(transactions), CHUNK_SIZE):
        chunk = transactions[chunk_start: chunk_start + CHUNK_SIZE]

        requests = [
            ClassificationRequest(
                transaction_id=txn.id,
                display_text=txn.display_text,
                amount_cents=txn.amount_cents,
                date=txn.date.isoformat(),
                account_role=txn.account.role,
                categories=category_dicts,
            )
            for txn in chunk
        ]

        results = classifier.classify_many(requests)

        for txn, result in zip(chunk, results):
            always_review = result.transaction_type in _ALWAYS_REVIEW_TYPES
            review_state = (
                "needs_review"
                if always_review or result.confidence < AUTO_ACCEPT_THRESHOLD
                else "accepted"
            )

            classification = apply_classification(
                session=session,
                transaction_id=txn.id,
                source="llm",
                transaction_type=result.transaction_type,
                category_id=result.category_id,
                merchant=result.merchant,
                rationale=result.rationale,
            )
            classification.review_state = review_state

            if review_state == "accepted":
                session.add(AuditEvent(
                    entity_type="classification",
                    entity_id=classification.id,
                    action="auto_accepted",
                    actor="llm",
                    before_state=None,
                    after_state={
                        "transaction_id": txn.id,
                        "transaction_type": result.transaction_type,
                        "confidence": result.confidence,
                        "review_state": "accepted",
                    },
                ))

        session.commit()
        logger.info(
            "classify_batch: committed chunk %d-%d of %d",
            chunk_start + 1, chunk_start + len(chunk), len(transactions),
        )
