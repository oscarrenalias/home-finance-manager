"""Settings page — accounts, rules, and provider configuration."""
import reflex as rx

from ui.components import shell


@rx.page(route="/settings", title="Settings | Home Finance")
def settings() -> rx.Component:
    return shell(
        rx.heading("Settings", size="7"),
        rx.text("Application settings — coming soon.", color_scheme="gray"),
    )
