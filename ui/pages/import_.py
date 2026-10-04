"""Import page — CSV statement upload and preview workflow."""
from __future__ import annotations

from datetime import date
from typing import Optional

import reflex as rx

from services.import_service import CommitResult, ImportPreview, ImportService
from storage.database import _get_session_factory
from storage.file_store import FileStore
from storage.models import Account
from ui.components import shell


def _make_service() -> ImportService:
    return ImportService(
        session_factory=_get_session_factory(),
        file_store=FileStore(),
    )


def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


class ImportState(rx.State):
    account_options: list[tuple[str, str]] = []
    selected_account_id: str = ""
    filename: str = ""
    coverage_start: str = ""
    coverage_end: str = ""
    completeness: bool = False
    committing: bool = False
    error_message: str = ""

    # Backend vars — server-side only, not synced to the browser (Reflex `_` prefix convention)
    _preview: Optional[ImportPreview] = None
    _commit_result: Optional[CommitResult] = None

    @rx.event
    def load_accounts(self) -> None:
        session = _get_session_factory()()
        try:
            accounts = (
                session.query(Account)
                .filter(Account.active == True)  # noqa: E712
                .order_by(Account.name)
                .all()
            )
            self.account_options = [(a.id, a.name) for a in accounts]
            if self.account_options and not self.selected_account_id:
                self.selected_account_id = self.account_options[0][0]
        finally:
            session.close()

    @rx.event(background=True)
    async def handle_upload(self, files: list[rx.UploadFile]) -> None:
        if not files:
            return

        file = files[0]
        filename = file.filename or "upload.csv"

        async with self:
            self.filename = filename
            self.error_message = ""
            self._preview = None
            self._commit_result = None
            account_id = self.selected_account_id
            coverage_start_str = self.coverage_start
            coverage_end_str = self.coverage_end
            completeness = self.completeness

        # Read bytes outside the state lock — avoids holding the lock during I/O
        data = await file.read()

        coverage_start = _parse_date(coverage_start_str)
        coverage_end = _parse_date(coverage_end_str)

        svc = _make_service()
        try:
            preview = svc.preview(
                account_id=account_id,
                filename=filename,
                data=data,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                completeness=completeness,
            )
            async with self:
                self._preview = preview
        except Exception as exc:
            async with self:
                self.error_message = str(exc)
        # `data` is a local variable — it goes out of scope here, not stored in state

    @rx.event(background=True)
    async def handle_commit(self) -> None:
        async with self:
            preview = self._preview
            if preview is None:
                return
            account_id = self.selected_account_id
            coverage_start_str = self.coverage_start
            coverage_end_str = self.coverage_end
            completeness = self.completeness
            self.committing = True
            self.error_message = ""

        coverage_start = _parse_date(coverage_start_str)
        coverage_end = _parse_date(coverage_end_str)

        svc = _make_service()
        try:
            result = svc.commit(
                account_id=account_id,
                idempotency_token=preview.idempotency_token,
                preview=preview,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                completeness=completeness,
            )
            async with self:
                self._commit_result = result
                self._preview = None
                self.committing = False
        except Exception as exc:
            async with self:
                self.error_message = str(exc)
                self.committing = False


@rx.page(
    route="/import",
    title="Import | Home Finance",
    on_load=ImportState.load_accounts,
)
def import_page() -> rx.Component:
    return shell(
        rx.heading("Import", size="7"),
        rx.text("CSV statement import — coming soon.", color_scheme="gray"),
    )
