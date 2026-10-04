"""Review page — pending classification decisions."""
import reflex as rx

from ui.components import shell


@rx.page(route="/review", title="Review | Home Finance")
def review() -> rx.Component:
    return shell(
        rx.heading("Review", size="7"),
        rx.text("Category review queue — coming soon.", color_scheme="gray"),
    )
