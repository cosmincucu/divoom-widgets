"""Widget registry.

A widget is a module exposing:
    fetch() -> dict            # network I/O only here; {'ok': False, ...} on failure
    render(data) -> PIL.Image  # pure, testable, 128x128
    interval_s() -> int        # refresh cadence
    summary(data) -> str       # one-line log summary
and optionally:
    metrics(data) -> list[(name, value, attrs)]   # gauges for the metrics store

Adding a widget = new module here + an entry in WIDGET_MODULES + a
"name:panel" item in the WIDGETS env var.
"""
from __future__ import annotations

from importlib import import_module

WIDGET_MODULES = {
    "claude_usage": "divoom_widgets.widgets.claude_usage",
    "codex_usage": "divoom_widgets.widgets.codex_usage",
    "energy": "divoom_widgets.widgets.energy",
}


def load(name: str):
    if name not in WIDGET_MODULES:
        raise KeyError(f"unknown widget {name!r}; known: {sorted(WIDGET_MODULES)}")
    return import_module(WIDGET_MODULES[name])
