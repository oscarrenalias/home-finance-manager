"""Immutable Money value type with integer-cent arithmetic. No float arithmetic."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    """A monetary amount stored as signed integer cents."""

    amount: int
    currency: str = "EUR"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int):
            raise TypeError(f"amount must be int, got {type(self.amount).__name__}")

    def add(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"Cannot add {self.currency} and {other.currency}")
        return Money(self.amount + other.amount, self.currency)

    def subtract(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"Cannot subtract {other.currency} from {self.currency}")
        return Money(self.amount - other.amount, self.currency)

    def negate(self) -> Money:
        return Money(-self.amount, self.currency)

    def __repr__(self) -> str:
        return f"Money(amount={self.amount}, currency={self.currency!r})"


def format_eur(money: Money) -> str:
    """Format a EUR Money value as a display string, e.g. '€12.34' or '€-2.55'."""
    if money.currency != "EUR":
        raise ValueError(f"format_eur only supports EUR, got {money.currency!r}")
    sign = "-" if money.amount < 0 else ""
    abs_cents = abs(money.amount)
    euros, cents = divmod(abs_cents, 100)
    return f"€{sign}{euros}.{cents:02d}"
