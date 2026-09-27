"""Render the README preview cards from sample data (no device, no network).

    python -m scripts.render_previews        # from the repo root; writes docs/preview-*.png

Each card is the widget's real render() output, scaled 2x (nearest) so the
pixels stay crisp on GitHub.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PIL import Image

from divoom_widgets.widgets import claude_usage, codex_usage, energy

DOCS = Path(__file__).resolve().parent.parent / "docs"
NOW = dt.datetime.now(dt.timezone.utc)


def _save(img: Image.Image, name: str) -> None:
    img.resize((img.width * 2, img.height * 2), Image.NEAREST).save(DOCS / name, optimize=True)


def main() -> None:
    claude = {
        "ok": True, "plan": "MAX 5x",
        "session_used": 34.0, "session_resets_at": NOW + dt.timedelta(hours=2, minutes=41),
        "week_used": 58.0, "week_resets_at": NOW + dt.timedelta(days=3, hours=5),
        "scoped": [{"label": "Opus", "used": 81.0, "active": True,
                    "resets_at": NOW + dt.timedelta(days=3, hours=5)}],
    }
    _save(claude_usage.render(claude), "preview-claude.png")

    codex = {
        "ok": True, "plan": "PRO", "plan_raw": "pro",
        "week_used": 42.0, "week_resets_at": NOW + dt.timedelta(days=4, hours=7),
        "window_minutes": 10080, "last_turn_at": NOW - dt.timedelta(minutes=12),
    }
    _save(codex_usage.render(codex), "preview-codex.png")

    _save(energy.render({"ok": True, "load_kw": 0.84, "solar_kw": 3.27, "soc_pct": 76.0}),
          "preview-energy.png")


if __name__ == "__main__":
    main()
