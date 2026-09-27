"""Shared 128x128 panel rendering helpers: palette, fonts, bars, text."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import Optional

from PIL import ImageFont

SIZE = 128

# Palette
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


@lru_cache(maxsize=64)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for name in _FONT_CANDIDATES[kind]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# Sizes tried by fit_font, largest first.
_FIT_SIZES = (19, 17, 15, 13, 11)


def fit_font(draw, text: str, kind: str, max_width: float,
             sizes: tuple = _FIT_SIZES) -> ImageFont.FreeTypeFont:
    """Largest font from `sizes` whose `text` fits `max_width`.

    A safety net so an unusually wide value (100%, a two-digit kW reading)
    shrinks instead of running into its label — panels are only 128px wide.
    """
    chosen = font(kind, sizes[-1])
    for size in sizes:
        candidate = font(kind, size)
        if draw.textlength(text, font=candidate) <= max_width:
            return candidate
    return chosen


class Fonts:
    def __init__(self) -> None:
        self.title = font("bold", 14)
        self.row = font("bold", 12)
        self.pct = font("bold", 19)
        self.small = font("regular", 11)
        self.tiny = font("regular", 9)


def severity_color(used: float) -> tuple[int, int, int]:
    """Color by consumption: low use = green ... high use = red."""
    if used >= 80:
        return RED
    if used >= 60:
        return AMBER
    return GREEN


def level_color(level: float) -> tuple[int, int, int]:
    """Color by remaining level (battery-style): high = green ... low = red."""
    if level < 20:
        return RED
    if level < 50:
        return AMBER
    return GREEN


def resets_in(when: Optional[dt.datetime]) -> str:
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


def right_text(draw, x_right, y, text, font, fill):
    draw.text((x_right - draw.textlength(text, font=font), y), text, font=font, fill=fill)


def right_text_bottom(draw, x_right, bottom_y, text, font, fill):
    """Right-align `text` with its ink bottom on `bottom_y`.

    Measured rather than anchored so that differently-sized values still sit
    on one line (fit_font may pick a smaller size for a wide value).
    """
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((x_right - box[2], bottom_y - box[3]), text, font=font, fill=fill)


def bar(draw, x, y, w, h, used_pct, color):
    used_pct = max(0.0, min(100.0, used_pct))
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=TRACK)
    fill_w = int(round(w * used_pct / 100.0))
    if fill_w >= h:
        draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=h // 2, fill=color)
    elif fill_w > 0:
        draw.rectangle([x, y, x + fill_w, y + h], fill=color)


def wrap_text(draw, text: str, font, max_width: float, max_lines: int = 2) -> list:
    """Greedy word-wrap for the small error strings on a 128px card."""
    words = str(text).split()
    lines: list = []
    for word in words:
        candidate = f"{lines[-1]} {word}" if lines else word
        if lines and draw.textlength(candidate, font=font) <= max_width:
            lines[-1] = candidate
        elif len(lines) < max_lines:
            lines.append(word)
        else:
            break
    return lines


def header(draw, fonts: Fonts, title: str, title_color, right: str = "") -> None:
    """Standard card header: colored title, optional right-aligned tag, rule."""
    draw.text((5, 2), title, font=fonts.title, fill=title_color)
    if right:
        right_text(draw, SIZE - 5, 5, right, fonts.small, GRAY)
    draw.line([5, 21, SIZE - 5, 21], fill=TRACK, width=1)


def usage_row(draw, fonts: Fonts, top: int, label: str, window: str,
              used: float, resets_at: Optional[dt.datetime]) -> None:
    """One usage window, 50px tall: label + used% on top, bar, reset hint.

    The window tag (5h / 7d) sits on the hint line rather than beside the
    label: at 100% a three-digit percentage would otherwise collide with it.
    Shared by every "percent of a rolling limit" card (Claude, Codex).
    """
    color = severity_color(used)
    draw.text((5, top), label, font=fonts.row, fill=GRAY)
    pct = f"{int(round(used))}%"
    gap = 6
    avail = SIZE - 10 - draw.textlength(label, font=fonts.row) - gap
    right_text_bottom(draw, SIZE - 5, top + 15, pct,
                      fit_font(draw, pct, "bold", avail), color)
    bar(draw, 5, top + 18, SIZE - 10, 9, used, color)
    reset = resets_in(resets_at)
    hint = f"{window} · resets {reset}" if reset else window
    draw.text((5, top + 30), hint, font=fonts.tiny, fill=DIM)


def usage_row_compact(draw, fonts: Fonts, top: int, label: str, window: str,
                      used: float, resets_at: Optional[dt.datetime]) -> None:
    """The 34px version of usage_row, for cards that show three windows."""
    color = severity_color(used)
    draw.text((5, top), label, font=fonts.small, fill=GRAY)
    pct = f"{int(round(used))}%"
    avail = SIZE - 10 - draw.textlength(label, font=fonts.small) - 6
    right_text_bottom(draw, SIZE - 5, top + 12, pct,
                      fit_font(draw, pct, "bold", avail, sizes=(15, 13, 11)), color)
    bar(draw, 5, top + 14, SIZE - 10, 7, used, color)
    reset = resets_in(resets_at)
    hint = f"{window} · resets {reset}" if reset else window
    draw.text((5, top + 23), hint, font=fonts.tiny, fill=DIM)
