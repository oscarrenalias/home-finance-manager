"""Raw import file storage — keeps uploaded bytes outside the database."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

_ACCOUNT_ID_RE = re.compile(r'^[A-Za-z0-9-]+$')


class FileStore:
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
