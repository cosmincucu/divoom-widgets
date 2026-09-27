import os
import time

import pytest

from divoom_widgets import widgets
from divoom_widgets.__main__ import (
    BACKOFF_CAP_SECONDS, _backoff, _child_cmd, _failing_for, _mark_success, main,
)
from divoom_widgets.config import parse_widgets


def test_parse_widgets_basic():
    assert parse_widgets("claude_usage:3,energy:2") == [("claude_usage", 3), ("energy", 2)]


def test_parse_widgets_clamps_and_defaults():
    assert parse_widgets("energy") == [("energy", 1)]        # missing panel -> 1
    assert parse_widgets("energy:9") == [("energy", 5)]      # clamped high
    assert parse_widgets("energy:0") == [("energy", 1)]      # clamped low
    assert parse_widgets("energy:x") == [("energy", 1)]      # junk panel -> 1


def test_parse_widgets_skips_malformed():
    assert parse_widgets("") == []
    assert parse_widgets(" , :3, energy:2 ") == [("energy", 2)]


def test_registry_loads_known_widgets():
    for name in widgets.WIDGET_MODULES:
        mod = widgets.load(name)
        assert callable(mod.fetch)
        assert callable(mod.render)
        assert mod.interval_s() >= 15
        assert callable(mod.summary)


def test_registry_rejects_unknown():
    with pytest.raises(KeyError):
        widgets.load("nope")


def test_child_cmd_forwards_push_and_save():
    base = _child_cmd("energy", "1.2.3.4", push=True, save=None)
    assert "--no-push" not in base and "--save" not in base
    assert base[-4:] == ["--widget", "energy", "--host", "1.2.3.4"]
    dry = _child_cmd("energy", "1.2.3.4", push=False, save="out.png")
    assert "--no-push" in dry
    assert dry[dry.index("--save") + 1] == "out.png"


def test_backoff_is_flat_until_a_widget_fails():
    assert _backoff(150, 0) == 150.0
    assert _backoff(150, 1) == 300.0
    assert _backoff(150, 2) == 600.0


def test_backoff_is_capped():
    # Without a cap, doubling from 150s reaches days. Two days of retrying a
    # dead Claude token every 150s is what got the caller rate-limited.
    assert _backoff(150, 20) == float(BACKOFF_CAP_SECONDS)
    assert _backoff(30, 99) == float(BACKOFF_CAP_SECONDS)


def test_failing_for_tracks_last_success(monkeypatch, tmp_path):
    import divoom_widgets.__main__ as m
    monkeypatch.setattr(m.config, "HEARTBEAT_FILE", str(tmp_path / "hb"))
    assert _failing_for("energy") is None      # never succeeded
    _mark_success("energy")
    assert _failing_for("energy") < 5
    # Age is read from the marker's mtime, so a long outage is detectable.
    path = m._last_success_path("energy")
    old = time.time() - 900
    os.utime(path, (old, old))
    assert _failing_for("energy") > 800


def test_main_rejects_widget_not_in_configured_set(monkeypatch, capsys):
    import divoom_widgets.__main__ as m
    monkeypatch.setattr(m.config, "WIDGETS", "claude_usage:3")
    rc = main(["--once", "--widget", "energy", "--no-push"])
    assert rc == 2
    assert "not in WIDGETS" in capsys.readouterr().out
