"""Reflex application entry point."""
import reflex as rx

# Import page modules to register their @rx.page decorators before app compiles.
from ui.pages import (  # noqa: F401
    ask,
    budgets,
    import_,
    overview,
    review,
    settings,
    transactions,
)

app = rx.App(
    theme=rx.theme(accent_color="teal"),
)
