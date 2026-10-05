"""Ask page — conversational finance analysis interface."""
import reflex as rx

from ui.components import shell


@rx.page(route="/ask", title="Ask | Home Finance")
def ask() -> rx.Component:
    return shell(
        rx.heading("Ask", size="7"),
        rx.text("Conversational finance assistant — coming soon.", color_scheme="gray"),
    )
