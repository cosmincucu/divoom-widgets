"""Render a 128x128 panel image showing Claude session + weekly usage."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

SIZE = 128

# Colors
BG = (8, 10, 14)
WHITE = (235, 238, 245)
GRAY = (150, 160, 175)
DIM = (95, 103, 116)
CLAUDE_BRAND = (217, 119, 87)   # Anthropic coral
GREEN = (63, 185, 80)
AMBER = (210, 153, 34)
RED = (248, 81, 73)
TRACK = (30, 34, 42)

_FONT_CANDIDATES = {
    "bold": ["arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"],
    "regular": ["arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"],
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for name in _FONT_CANDIDATES[kind]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class Fonts:
    def __init__(self) -> None:
        self.title = _font("bold", 14)
        self.row = _font("bold", 12)
        self.pct = _font("bold", 19)
        self.small = _font("regular", 11)
        self.tiny = _font("regular", 9)


def severity_color(used: float) -> tuple[int, int, int]:
    """Color by consumption: low use = green ... high use = red."""
    if used >= 80:
        return RED
    if used >= 60:
        return AMBER
    return GREEN


def _resets_in(when: Optional[dt.datetime]) -> str:
    if not when:
        return ""
    secs = int((when - dt.datetime.now(dt.timezone.utc)).total_seconds())
    if secs <= 0:
        return "now"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _right(draw, x_right, y, text, font, fill):
    draw.text((x_right - draw.textlength(text, font=font), y), text, font=font, fill=fill)


def _bar(draw, x, y, w, h, used_pct, color):
    used_pct = max(0.0, min(100.0, used_pct))
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=TRACK)
    fill_w = int(round(w * used_pct / 100.0))
    if fill_w >= h:
        draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=h // 2, fill=color)
    elif fill_w > 0:
        draw.rectangle([x, y, x + fill_w, y + h], fill=color)


def _row(draw, fonts, top, label, used, resets_at):
    """One usage window: label + used% on top line, bar, reset hint below."""
    color = severity_color(used)
    draw.text((5, top), label, font=fonts.row, fill=GRAY)
    _right(draw, SIZE - 5, top - 4, f"{int(round(used))}%", fonts.pct, color)
    _bar(draw, 5, top + 18, SIZE - 10, 9, used, color)
    reset = _resets_in(resets_at)
    if reset:
        draw.text((5, top + 30), f"resets {reset}", font=fonts.tiny, fill=DIM)


def render(claude: dict) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    draw = ImageDraw.Draw(img)
    fonts = Fonts()

    # --- Header ---
    draw.text((5, 2), "CLAUDE", font=fonts.title, fill=CLAUDE_BRAND)
    if claude.get("ok") and claude.get("plan"):
        _right(draw, SIZE - 5, 5, claude["plan"], fonts.small, GRAY)
    draw.line([5, 21, SIZE - 5, 21], fill=TRACK, width=1)

    if not claude.get("ok"):
        draw.text((5, 52), "no data", font=fonts.title, fill=GRAY)
        draw.text((5, 70), str(claude.get("error", ""))[:20], font=fonts.tiny, fill=DIM)
        return img

    # --- Session (5-hour) ---
    _row(draw, fonts, 28, "SESSION  5h",
         claude.get("session_used", 0.0), claude.get("session_resets_at"))
    # --- Weekly (7-day) ---
    _row(draw, fonts, 78, "WEEKLY  7d",
         claude.get("week_used", 0.0), claude.get("week_resets_at"))

    return img
