"""Overview page — monthly household summary."""
import reflex as rx

from ui.components import shell


@rx.page(route="/", title="Overview | Home Finance")
def overview() -> rx.Component:
    return shell(
        rx.heading("Overview", size="7"),
        rx.text("Household financial overview — coming soon.", color_scheme="gray"),
    )
