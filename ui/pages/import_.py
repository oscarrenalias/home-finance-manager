"""Import page — CSV statement upload and preview workflow."""
import reflex as rx

from ui.components import shell


@rx.page(route="/import", title="Import | Home Finance")
def import_page() -> rx.Component:
    return shell(
        rx.heading("Import", size="7"),
        rx.text("CSV statement import — coming soon.", color_scheme="gray"),
    )
