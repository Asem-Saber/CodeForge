"""The startup welcome panel."""

WORDMARK = (
    "█████ █████ ████  █████ █████ █████ ████  █████ █████",
    "█     █   █ █   █ █     █     █   █ █   █ █     █    ",
    "█     █   █ █   █ ████  ████  █   █ ████  █  ██ ████ ",
    "█     █   █ █   █ █     █     █   █ █  █  █   █ █    ",
    "█████ █████ ████  █████ █     █████ █   █ █████ █████",
)

WORDMARK_ASCII = tuple(row.replace("█", "#") for row in WORDMARK)


def wordmark_for(console) -> tuple[str, ...]:
    """The block art, or an ASCII copy on a terminal that cannot draw it."""
    if getattr(console, "legacy_windows", False):
        return WORDMARK_ASCII
    return WORDMARK
