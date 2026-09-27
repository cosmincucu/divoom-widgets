"""Deprecated: moved to divoom_widgets.render_kit (helpers) and
divoom_widgets.widgets.claude_usage (the card)."""
from divoom_widgets.render_kit import (  # noqa: F401
    AMBER, BG, CLAUDE_BRAND, DIM, GRAY, GREEN, RED, SIZE, TRACK, WHITE,
    Fonts, severity_color,
)
from divoom_widgets.render_kit import resets_in as _resets_in  # noqa: F401
from divoom_widgets.widgets.claude_usage import render  # noqa: F401
