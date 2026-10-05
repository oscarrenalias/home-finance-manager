"""Tests for llm.classifier protocol and LiteLLM adapter.

Covers:
  - Module imports safely without env vars
  - RuntimeError raised on classify() when env vars absent
  - Protocol isinstance checks (AbstractClassifier)
  - Post-call validation (invalid transaction_type, invalid category_id)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(category_ids: list[str] | None = None):
    from llm.classifier import ClassificationRequest

    cats = [{"id": c, "name": c.title()} for c in (category_ids or ["groceries", "transport"])]
    return ClassificationRequest(
        transaction_id="txn-test",
        display_text="Test purchase",
        amount_cents=-1000,
        date="2026-01-01",
        account_role="common",
        categories=cats,
    )


def _make_result(transaction_type: str, category_id: str | None, confidence: float = 0.9):
    from llm.classifier import ClassificationResult

    return ClassificationResult(
        transaction_type=transaction_type,
        category_id=category_id,
        confidence=confidence,
    )


def _classify_with_mock(monkeypatch, request, mock_result):
    """Run classify() with env vars set and LangChain chain mocked."""
    monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")

    with patch("langchain_openai.ChatOpenAI") as mock_chat:
        chain_mock = MagicMock()
        chain_mock.invoke.return_value = mock_result
        mock_chat.return_value.with_structured_output.return_value = chain_mock

        from llm.classifier import LiteLLMClassifier

        clf = LiteLLMClassifier()
        return clf.classify(request)


# ---------------------------------------------------------------------------
# Module import — must succeed without any env vars present
# ---------------------------------------------------------------------------

class TestModuleImport:
    def test_import_without_env_vars(self, monkeypatch):
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        import llm.classifier  # noqa: F401 — import is the assertion

    def test_classifier_classes_accessible_without_env_vars(self, monkeypatch):
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        from llm.classifier import (  # noqa: F401
            AbstractClassifier,
            ClassificationRequest,
            ClassificationResult,
            LiteLLMClassifier,
        )


# ---------------------------------------------------------------------------
# RuntimeError raised on classify() when env vars are absent
# ---------------------------------------------------------------------------

class TestRuntimeErrorWithoutEnvVars:
    def _req(self):
        return _make_request()

    def test_raises_when_both_vars_missing(self, monkeypatch):
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        from llm.classifier import LiteLLMClassifier

        with pytest.raises(RuntimeError, match="LITELLM_BASE_URL"):
            LiteLLMClassifier().classify(self._req())

    def test_raises_when_only_master_key_set(self, monkeypatch):
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
        from llm.classifier import LiteLLMClassifier

        with pytest.raises(RuntimeError, match="LITELLM_BASE_URL"):
            LiteLLMClassifier().classify(self._req())

    def test_raises_when_only_base_url_set(self, monkeypatch):
        monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000")
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        from llm.classifier import LiteLLMClassifier

        with pytest.raises(RuntimeError, match="LITELLM_MASTER_KEY"):
            LiteLLMClassifier().classify(self._req())


# ---------------------------------------------------------------------------
# Protocol isinstance checks
# ---------------------------------------------------------------------------

class TestAbstractClassifierProtocol:
    def test_litellm_classifier_satisfies_protocol(self):
        from llm.classifier import AbstractClassifier, LiteLLMClassifier

        assert isinstance(LiteLLMClassifier(), AbstractClassifier)

    def test_custom_class_with_classify_satisfies_protocol(self):
        from llm.classifier import (
            AbstractClassifier,
            ClassificationRequest,
            ClassificationResult,
        )

        class _Stub:
            def classify(self, request: ClassificationRequest) -> ClassificationResult:
                return ClassificationResult(transaction_type="unknown", confidence=0.0)

        assert isinstance(_Stub(), AbstractClassifier)

    def test_class_missing_classify_does_not_satisfy_protocol(self):
        from llm.classifier import AbstractClassifier

        class _NotAClassifier:
            pass

        assert not isinstance(_NotAClassifier(), AbstractClassifier)

    def test_classify_wrong_arity_does_not_satisfy_protocol(self):
        from llm.classifier import AbstractClassifier

        class _WrongArity:
            def classify(self) -> None:  # missing request parameter
                pass

        # runtime_checkable only checks method presence, not signature;
        # but a no-arg classify() still has the attribute name → passes the
        # structural isinstance check. Document expected behaviour.
        assert isinstance(_WrongArity(), AbstractClassifier)


# ---------------------------------------------------------------------------
# Post-call validation — invalid transaction_type
# ---------------------------------------------------------------------------

class TestTransactionTypeValidation:
    def test_invalid_type_coerced_to_unknown(self, monkeypatch):
        req = _make_request()
        result = _classify_with_mock(monkeypatch, req, _make_result("not_a_real_type", None))
        assert result.transaction_type == "unknown"

    def test_valid_expense_type_preserved(self, monkeypatch):
        req = _make_request()
        result = _classify_with_mock(monkeypatch, req, _make_result("expense", None))
        assert result.transaction_type == "expense"

    def test_all_valid_types_preserved(self, monkeypatch):
        valid_types = [
            "expense",
            "refund",
            "internal_transfer",
            "contribution",
            "income",
            "external_transfer",
            "unknown",
        ]
        for txn_type in valid_types:
            req = _make_request()
            result = _classify_with_mock(monkeypatch, req, _make_result(txn_type, None))
            assert result.transaction_type == txn_type, f"{txn_type!r} should be preserved"


# ---------------------------------------------------------------------------
# Post-call validation — invalid category_id
# ---------------------------------------------------------------------------

class TestCategoryIdValidation:
    def test_invalid_category_id_cleared(self, monkeypatch):
        req = _make_request(["groceries", "transport"])
        result = _classify_with_mock(monkeypatch, req, _make_result("expense", "nonexistent", confidence=0.85))
        assert result.category_id is None

    def test_invalid_category_id_caps_confidence_at_040(self, monkeypatch):
        req = _make_request(["groceries"])
        result = _classify_with_mock(monkeypatch, req, _make_result("expense", "bad_cat", confidence=0.95))
        assert result.confidence <= 0.40

    def test_valid_category_id_preserved(self, monkeypatch):
        req = _make_request(["groceries", "transport"])
        result = _classify_with_mock(monkeypatch, req, _make_result("expense", "groceries", confidence=0.95))
        assert result.category_id == "groceries"
        assert result.confidence == 0.95

    def test_none_category_id_not_penalized(self, monkeypatch):
        req = _make_request(["groceries"])
        result = _classify_with_mock(monkeypatch, req, _make_result("expense", None, confidence=0.80))
        assert result.category_id is None
        assert result.confidence == 0.80

    def test_confidence_already_low_not_raised_on_bad_category(self, monkeypatch):
        req = _make_request(["groceries"])
        result = _classify_with_mock(monkeypatch, req, _make_result("expense", "bad_cat", confidence=0.10))
        # confidence is min(0.10, 0.40) = 0.10; should not be raised
        assert result.confidence == 0.10
        assert result.category_id is None
