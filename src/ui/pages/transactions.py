"""Transactions page — browsable ledger with filters."""
import reflex as rx

from ui.components import shell


@rx.page(route="/transactions", title="Transactions | Home Finance")
def transactions() -> rx.Component:
    return shell(
        rx.heading("Transactions", size="7"),
        rx.text("Transaction ledger — coming soon.", color_scheme="gray"),
    )
