import datetime as dt

from PIL import Image

from usage_widget import render


def test_severity_color_thresholds():
    assert render.severity_color(0) == render.GREEN
    assert render.severity_color(59) == render.GREEN
    assert render.severity_color(60) == render.AMBER
    assert render.severity_color(79) == render.AMBER
    assert render.severity_color(80) == render.RED
    assert render.severity_color(100) == render.RED


def test_resets_in_formats():
    now = dt.datetime.now(dt.timezone.utc)
    assert render._resets_in(now + dt.timedelta(days=2, hours=3)) == "2d 3h"
    assert render._resets_in(now + dt.timedelta(hours=4, minutes=30)).startswith("4h")
    assert render._resets_in(now + dt.timedelta(minutes=5)).endswith("m")
    assert render._resets_in(now - dt.timedelta(minutes=1)) == "now"
    assert render._resets_in(None) == ""


def test_render_ok_returns_128_rgb():
    claude = {
        "ok": True,
        "plan": "MAX 5x",
        "session_used": 13.0,
        "session_resets_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4),
        "week_used": 2.0,
        "week_resets_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=6),
    }
    img = render.render(claude)
    assert isinstance(img, Image.Image)
    assert img.size == (render.SIZE, render.SIZE) == (128, 128)
    assert img.mode == "RGB"


def test_render_error_state_still_renders():
    img = render.render({"ok": False, "error": "no access token"})
    assert img.size == (128, 128)
