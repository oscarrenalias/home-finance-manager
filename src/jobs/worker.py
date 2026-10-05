"""Background job worker — polls for pending jobs and executes them."""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from storage.database import get_session
from storage.models import Job

logger = logging.getLogger(__name__)

_running = True

_LEASE_DURATION = timedelta(minutes=5)
_POLL_INTERVAL = 5
_MAX_RETRIES = 3

HandlerFn = Callable[[Session, Job], None]


def _handle_shutdown(_signum, _frame):
    global _running
    logger.info("Shutdown signal received, stopping worker.")
    _running = False


def _claim_next_job(session: Session) -> Optional[Job]:
    """Atomically claim the next claimable pending job.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent workers don't race
    on the same row. On SQLite this degrades gracefully (SKIP LOCKED is a no-op).
    Caller is responsible for committing the session to persist the lease.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    job = session.scalars(
        select(Job)
        .where(
            Job.state == "pending",
            or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < now),
        )
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    job.state = "in_progress"
    job.lease_expires_at = now + _LEASE_DURATION
    return job


def _build_handlers() -> dict[str, HandlerFn]:
    from jobs.classify_batch import handle_classify_batch
    from llm.classifier import LiteLLMClassifier

    classifier = LiteLLMClassifier()

    def _dispatch_classify_batch(session: Session, job: Job) -> None:
        handle_classify_batch(session, job, classifier)

    return {
        "classify_batch": _dispatch_classify_batch,
    }


def _execute_job(job_id: str, handlers: dict[str, HandlerFn]) -> None:
    """Run a claimed job and update its final state."""
    caught: Optional[Exception] = None

    try:
        with get_session() as session:
            job = session.get(Job, job_id)
            if job is None:
                logger.warning("Job %s not found in DB, skipping.", job_id)
                return
            handler = handlers.get(job.kind)
            if handler is None:
                logger.warning("Unknown job kind '%s' (id=%s), marking done.", job.kind, job_id)
                job.state = "done"
                job.lease_expires_at = None
                return  # get_session commits on exit
            handler(session, job)
            job.state = "done"
            job.lease_expires_at = None
            # get_session commits on exit
    except Exception as exc:
        logger.exception("Job %s raised an exception.", job_id)
        caught = exc

    if caught is not None:
        # The execution session rolled back; open a fresh session to record the outcome.
        with get_session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.retry_count += 1
            job.lease_expires_at = None
            if job.retry_count < _MAX_RETRIES:
                job.state = "pending"
            else:
                job.state = "failed"
                job.error_summary = str(caught)


def run() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    logger.info("Worker started. Waiting for jobs.")

    handlers = _build_handlers()

    while _running:
        job_id: Optional[str] = None

        with get_session() as session:
            job = _claim_next_job(session)
            if job is not None:
                job_id = job.id
        # get_session commits the claim (in_progress + lease) before we execute

        if job_id is not None:
            _execute_job(job_id, handlers)

        time.sleep(_POLL_INTERVAL)

    logger.info("Worker stopped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
