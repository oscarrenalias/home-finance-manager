"""Tests for storage.file_store.FileStore — path construction, sanitization, and exists()."""

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from storage.file_store import FileStore


@pytest.fixture()
def store(tmp_path):
    return FileStore(root_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# Path construction
# ---------------------------------------------------------------------------

class TestPathConstruction:
    def test_save_creates_account_subdir(self, store, tmp_path):
        store.save("acct-1", "file.csv", b"data")
        assert (tmp_path / "acct-1").is_dir()

    def test_save_path_uses_sha256_hex(self, store, tmp_path):
        data = b"hello world"
        expected_sha = hashlib.sha256(data).hexdigest()
        path_str, sha = store.save("acct-1", "file.csv", data)
        assert sha == expected_sha
        assert path_str == str(tmp_path / "acct-1" / f"{expected_sha}.bin")

    def test_save_returns_path_that_exists(self, store):
        path_str, _ = store.save("acct-1", "file.csv", b"bytes")
        assert Path(path_str).exists()

    def test_default_root_falls_back_to_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IMPORT_FILES_DIR", str(tmp_path))
        s = FileStore()
        assert s._root == tmp_path

    def test_default_root_fallback_when_no_env(self, monkeypatch):
        monkeypatch.delenv("IMPORT_FILES_DIR", raising=False)
        s = FileStore()
        assert s._root == Path("/data/imports")


# ---------------------------------------------------------------------------
# Sanitization — account_id
# ---------------------------------------------------------------------------

class TestAccountIdSanitization:
    @pytest.mark.parametrize("bad_id", [
        "../evil",
        "acct/sub",
        "acct id",
        "acct@id",
        "",
    ])
    def test_save_rejects_bad_account_id(self, store, bad_id):
        with pytest.raises(ValueError, match="Invalid account_id"):
            store.save(bad_id, "file.csv", b"data")

    @pytest.mark.parametrize("good_id", [
        "acct1",
        "ACCT-2",
        "123",
        "a-b-c",
    ])
    def test_save_accepts_valid_account_id(self, store, good_id):
        path_str, sha = store.save(good_id, "file.csv", b"data")
        assert Path(path_str).exists()

    def test_exists_rejects_bad_account_id(self, store):
        with pytest.raises(ValueError, match="Invalid account_id"):
            store.exists("deadbeef", "../evil")


# ---------------------------------------------------------------------------
# Sanitization — filename
# ---------------------------------------------------------------------------

class TestFilenameSanitization:
    @pytest.mark.parametrize("bad_name", [
        "../escape.csv",
        "sub/dir.csv",
        "a/../b.csv",
    ])
    def test_save_rejects_path_traversal_filename(self, store, bad_name):
        with pytest.raises(ValueError, match="Invalid filename"):
            store.save("acct-1", bad_name, b"data")

    def test_save_accepts_simple_filename(self, store):
        path_str, _ = store.save("acct-1", "export.csv", b"data")
        assert Path(path_str).exists()


# ---------------------------------------------------------------------------
# exists()
# ---------------------------------------------------------------------------

class TestExists:
    def test_exists_returns_false_before_save(self, store):
        sha = hashlib.sha256(b"not-saved").hexdigest()
        assert store.exists(sha, "acct-1") is False

    def test_exists_returns_true_after_save(self, store):
        data = b"some csv content"
        _, sha = store.save("acct-1", "data.csv", data)
        assert store.exists(sha, "acct-1") is True

    def test_exists_is_false_for_different_account(self, store):
        data = b"shared data"
        _, sha = store.save("acct-a", "data.csv", data)
        assert store.exists(sha, "acct-b") is False


# ---------------------------------------------------------------------------
# Idempotent save
# ---------------------------------------------------------------------------

class TestIdempotentSave:
    def test_second_save_returns_same_path(self, store):
        data = b"idempotent"
        path1, sha1 = store.save("acct-1", "a.csv", data)
        path2, sha2 = store.save("acct-1", "b.csv", data)
        assert path1 == path2
        assert sha1 == sha2

    def test_second_save_does_not_corrupt_file(self, store):
        data = b"idempotent content"
        path_str, _ = store.save("acct-1", "a.csv", data)
        store.save("acct-1", "a.csv", data)
        assert Path(path_str).read_bytes() == data
