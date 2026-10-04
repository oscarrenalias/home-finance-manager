"""Raw import file storage — keeps uploaded bytes outside the database."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

_ACCOUNT_ID_RE = re.compile(r'^[A-Za-z0-9-]+$')


class FileStore:
    """Content-addressed store for raw import bytes, kept outside the database.

    Files are stored under <root>/<account_id>/<sha256>.bin.  The SHA-256 hash
    acts as the content address: identical uploads land on the same path and are
    written only once.  The database stores the hash as a foreign reference;
    the bytes live here on disk.
    """

    def __init__(self, root_dir: str | None = None) -> None:
        if root_dir is None:
            root_dir = os.environ.get("IMPORT_FILES_DIR", "/data/imports")
        self._root = Path(root_dir)

    def _validate_account_id(self, account_id: str) -> None:
        if not _ACCOUNT_ID_RE.match(account_id):
            raise ValueError(
                f"Invalid account_id {account_id!r}: only alphanumeric characters and hyphens are allowed"
            )

    def _validate_filename(self, filename: str) -> None:
        """Reject path components that could escape the account directory (path traversal)."""
        if ".." in filename or "/" in filename:
            raise ValueError(
                f"Invalid filename {filename!r}: must not contain '..' or '/'"
            )

    def _path_for(self, account_id: str, sha256_hex: str) -> Path:
        return self._root / account_id / f"{sha256_hex}.bin"

    def exists(self, sha256_hex: str, account_id: str) -> bool:
        self._validate_account_id(account_id)
        return self._path_for(account_id, sha256_hex).exists()

    def save(self, account_id: str, filename: str, data: bytes) -> tuple[str, str]:
        """Write data to <root>/<account_id>/<sha256>.bin atomically.

        Atomic-write pattern: data is written to a temporary file in the same
        directory as the destination, then renamed with os.replace().  On POSIX,
        os.replace() is a single syscall (rename(2)) that is atomic with respect
        to readers — no reader ever observes a partial write.  If the write fails
        the temp file is cleaned up and the exception is re-raised.

        If the destination already exists (same content reimported), the write
        step is skipped entirely; the existing file is reused.

        Returns (path_str, sha256_hex).
        """
        self._validate_account_id(account_id)
        self._validate_filename(filename)

        sha256_hex = hashlib.sha256(data).hexdigest()
        dest = self._path_for(account_id, sha256_hex)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not dest.exists():
            fd, tmp_path = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp_path, dest)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        return (str(dest), sha256_hex)
