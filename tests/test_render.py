import datetime as dt

from PIL import Image, ImageDraw

from divoom_widgets import render_kit
from divoom_widgets.widgets import claude_usage


def test_severity_color_thresholds():
    assert render_kit.severity_color(0) == render_kit.GREEN
    assert render_kit.severity_color(59) == render_kit.GREEN
    assert render_kit.severity_color(60) == render_kit.AMBER
    assert render_kit.severity_color(79) == render_kit.AMBER
    assert render_kit.severity_color(80) == render_kit.RED
    assert render_kit.severity_color(100) == render_kit.RED


def test_level_color_thresholds():
    assert render_kit.level_color(100) == render_kit.GREEN
    assert render_kit.level_color(50) == render_kit.GREEN
    assert render_kit.level_color(49) == render_kit.AMBER
    assert render_kit.level_color(20) == render_kit.AMBER
    assert render_kit.level_color(19) == render_kit.RED
    assert render_kit.level_color(0) == render_kit.RED


def test_resets_in_formats():
    now = dt.datetime.now(dt.timezone.utc)
    # +1 min of slack: resets_in reads the clock after `now`, and truncates.
    assert render_kit.resets_in(now + dt.timedelta(days=2, hours=3, minutes=1)) == "2d 3h"
    assert render_kit.resets_in(now + dt.timedelta(hours=4, minutes=30)).startswith("4h")
    assert render_kit.resets_in(now + dt.timedelta(minutes=5)).endswith("m")
    assert render_kit.resets_in(now - dt.timedelta(minutes=1)) == "now"
    assert render_kit.resets_in(None) == ""


def test_render_ok_returns_128_rgb():
    claude = {
        "ok": True,
        "plan": "MAX 5x",
        "session_used": 13.0,
        "session_resets_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4),
        "week_used": 2.0,
        "week_resets_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=6),
    }
    img = claude_usage.render(claude)
    assert isinstance(img, Image.Image)
    assert img.size == (render_kit.SIZE, render_kit.SIZE) == (128, 128)
    assert img.mode == "RGB"


def test_render_error_state_still_renders():
    img = claude_usage.render({"ok": False, "error": "no access token"})
    assert img.size == (128, 128)
    img = claude_usage.render({"ok": False, "error": "auth expired"})
    assert img.size == (128, 128)


def test_wrap_text_fits_the_card_and_is_bounded():
    draw = ImageDraw.Draw(Image.new("RGB", (128, 128)))
    fonts = render_kit.Fonts()
    lines = render_kit.wrap_text(draw, "auth expired", fonts.tiny, 118)
    assert lines == ["auth expired"]
    # Long messages wrap rather than being cut mid-word, and stay bounded.
    lines = render_kit.wrap_text(draw, "connection timed out contacting the api", fonts.tiny, 118)
    assert len(lines) <= 2
    for line in lines:
        assert draw.textlength(line, font=fonts.tiny) <= 118


def test_fit_font_shrinks_only_when_needed():
    draw = ImageDraw.Draw(Image.new("RGB", (128, 128)))
    assert render_kit.fit_font(draw, "5%", "bold", 120).size == 19
    # A width between the smallest and the largest size's rendering, measured
    # rather than hard-coded: glyph widths differ between Arial and DejaVu.
    smallest = draw.textlength("100%", font=render_kit.font("bold", 11))
    largest = draw.textlength("100%", font=render_kit.font("bold", 19))
    width = (smallest + largest) / 2
    narrow = render_kit.fit_font(draw, "100%", "bold", width)
    assert narrow.size < 19
    assert draw.textlength("100%", font=narrow) <= width


def test_worst_case_values_never_overlap_their_label():
    """100% next to the widest label must still fit the 128px panel."""
    draw = ImageDraw.Draw(Image.new("RGB", (128, 128)))
    fonts = render_kit.Fonts()
    gap = 6
    for label in ("SESSION", "WEEKLY", "BATT"):
        avail = render_kit.SIZE - 10 - draw.textlength(label, font=fonts.row) - gap
        chosen = render_kit.fit_font(draw, "100%", "bold", avail)
        assert draw.textlength("100%", font=chosen) <= avail, label


def test_right_text_bottom_aligns_ink_bottom():
    img = Image.new("RGB", (128, 128))
    draw = ImageDraw.Draw(img)
    for size in (19, 13):
        render_kit.right_text_bottom(
            draw, 123, 43, "100%", render_kit.font("bold", size), render_kit.WHITE)
        box = draw.textbbox((0, 0), "100%", font=render_kit.font("bold", size))
        assert box[3] <= 43


def test_legacy_render_import_still_works():
    from usage_widget import render as legacy
    assert legacy.render is claude_usage.render
    assert legacy.severity_color is render_kit.severity_color
