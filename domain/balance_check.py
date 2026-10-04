"""Balance check data structures and logic for import validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.parser import ParsedRow


@dataclass
class BalanceMismatch:
    row_number_a: int
    row_number_b: int
    expected_cents: int
    actual_cents: int


@dataclass
class BalanceCheckResult:
    checked_pairs: int
    mismatches: list[BalanceMismatch] = field(default_factory=list)
    inconclusive_reasons: list[str] = field(default_factory=list)


def check_balances(rows: list[ParsedRow]) -> BalanceCheckResult:
    """Check balance arithmetic for executed rows with non-null balances.

    Consecutive pair invariant (ascending): balance_a + amount_b == balance_b.
    Same-day pairs are inconclusive — appended to inconclusive_reasons, not
    counted as mismatches. Pending rows are excluded entirely.
    """
    from domain.parser import ParsedRow  # noqa: F401 — resolve forward ref at runtime

    usable = [
        r for r in rows
        if not r.is_pending and r.parsed_balance_cents is not None
    ]

    if len(usable) < 2:
        return BalanceCheckResult(checked_pairs=0)

    # Normalise to ascending (chronological) order.
    # Compare first vs last date; ties leave the list as-is.
    if usable[0].parsed_date > usable[-1].parsed_date:
        usable = list(reversed(usable))

    mismatches: list[BalanceMismatch] = []
    inconclusive_reasons: list[str] = []
    checked_pairs = 0

    for i in range(len(usable) - 1):
        a = usable[i]
        b = usable[i + 1]

        if a.parsed_date == b.parsed_date:
            inconclusive_reasons.append(
                f"Same-day ambiguity: rows {a.row_number} and {b.row_number} "
                f"both dated {a.parsed_date}"
            )
            continue

        expected = a.parsed_balance_cents + b.parsed_amount_cents
        actual = b.parsed_balance_cents
        checked_pairs += 1

        if expected != actual:
            mismatches.append(
                BalanceMismatch(
                    row_number_a=a.row_number,
                    row_number_b=b.row_number,
                    expected_cents=expected,
                    actual_cents=actual,
                )
            )

    return BalanceCheckResult(
        checked_pairs=checked_pairs,
        mismatches=mismatches,
        inconclusive_reasons=inconclusive_reasons,
    )
