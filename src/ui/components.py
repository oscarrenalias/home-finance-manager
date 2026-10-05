"""Shared UI components: sidebar navigation and page shell."""
import reflex as rx

_NAV_ITEMS: list[tuple[str, str]] = [
    ("Overview", "/"),
    ("Transactions", "/transactions"),
    ("Import", "/import"),
    ("Review", "/review"),
    ("Budgets", "/budgets"),
    ("Ask", "/ask"),
    ("Settings", "/settings"),
]


def _nav_link(label: str, href: str) -> rx.Component:
    return rx.link(
        label,
        href=href,
        width="100%",
        padding="0.6em 1em",
        border_radius="0.4em",
        color="inherit",
        text_decoration="none",
        _hover={"background": "var(--accent-2)"},
        display="block",
    )


def sidebar() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.box(
                rx.text(
                    rx.text.span("Home", weight="bold"),
                    rx.text.span("finance", weight="bold", color_scheme="teal"),
                    size="5",
                ),
                rx.text("Household workspace", size="1", color_scheme="gray"),
                padding_bottom="0.5em",
            ),
            rx.vstack(
                *[_nav_link(label, href) for label, href in _NAV_ITEMS],
                align="start",
                width="100%",
                gap="0.25em",
            ),
            align="start",
            height="100%",
            padding="1.5em 1em",
            gap="1.5em",
        ),
        width="180px",
        min_height="100vh",
        border_right="1px solid var(--gray-4)",
        background="var(--color-panel)",
        flex_shrink="0",
    )


def shell(*content: rx.Component) -> rx.Component:
    """Wrap page content with the persistent sidebar navigation."""
    return rx.flex(
        sidebar(),
        rx.box(*content, flex="1", padding="1.5em", min_width="0"),
        min_height="100vh",
        background="var(--gray-1)",
    )
