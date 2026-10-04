"""Budgets page — monthly budget configuration and progress."""
import reflex as rx

from ui.components import shell


@rx.page(route="/budgets", title="Budgets | Home Finance")
def budgets() -> rx.Component:
    return shell(
        rx.heading("Budgets", size="7"),
        rx.text("Budget management — coming soon.", color_scheme="gray"),
    )
