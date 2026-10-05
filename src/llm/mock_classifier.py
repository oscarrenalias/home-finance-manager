"""Deterministic mock classifier for tests and offline development.

No network calls are made under any code path.
"""

from __future__ import annotations

from .classifier import AbstractClassifier, ClassificationRequest, ClassificationResult

_RATIONALE = "Mock classification applied deterministic rules; no model was consulted."


class MockClassifier:
    """Implements AbstractClassifier with fully deterministic, rule-based logic.

    Rule precedence (first match wins):
      1. 'TRANSFER' in display_text (case-insensitive) → internal_transfer, 0.95
      2. amount_cents < 0                              → expense,           0.90
      3. amount_cents >= 100                           → contribution,      0.75
      4. default                                       → unknown,           0.50
    """

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        if "transfer" in request.display_text.lower():
            return ClassificationResult(
                transaction_type="internal_transfer",
                confidence=0.95,
                rationale=_RATIONALE,
            )

        if request.amount_cents < 0:
            return ClassificationResult(
                transaction_type="expense",
                confidence=0.90,
                rationale=_RATIONALE,
            )

        if request.amount_cents >= 100:
            return ClassificationResult(
                transaction_type="contribution",
                confidence=0.75,
                rationale=_RATIONALE,
            )

        return ClassificationResult(
            transaction_type="unknown",
            confidence=0.50,
            rationale=_RATIONALE,
        )

    def classify_many(self, requests: list[ClassificationRequest]) -> list[ClassificationResult]:
        return [self.classify(r) for r in requests]


# Verify protocol conformance at import time.
_: AbstractClassifier = MockClassifier()
