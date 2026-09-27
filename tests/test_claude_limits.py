"""The usage endpoint's `limits` shape and the metrics the Claude widget
derives from it."""
from divoom_widgets.widgets import claude_usage as claude

# Trimmed to the fields the widget reads; illustrative values (session 21%,
# all models 25%, Fable 49%, weekly reset 03:00Z).
LIVE = {
    "five_hour": {"utilization": 21.0, "resets_at": "2026-09-07T12:49:59+00:00"},
    "seven_day": {"utilization": 25.0, "resets_at": "2026-09-10T02:59:59+00:00"},
    "seven_day_opus": None,
    "seven_day_sonnet": None,
    "seven_day_oauth_apps": None,
    "limits": [
        {"kind": "session", "group": "session", "percent": 21, "severity": "normal",
         "resets_at": "2026-09-07T12:49:59+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_all", "group": "weekly", "percent": 25, "severity": "normal",
         "resets_at": "2026-09-10T02:59:59+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 49, "severity": "normal",
         "resets_at": "2026-09-10T02:59:59+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
         "is_active": True},
    ],
}

LEGACY = {
    "five_hour": {"utilization": 13.0, "resets_at": "2026-06-19T01:10:00+00:00"},
    "seven_day": {"utilization": 2.0, "resets_at": "2026-06-25T03:00:00+00:00"},
    "seven_day_opus": None,
    "seven_day_sonnet": {"utilization": 7.0, "resets_at": None},
}


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_windows_prefers_limits_and_names_the_scoped_model():
    win = claude.windows(LIVE)
    assert win["five_hour"]["used"] == 21.0
    assert win["seven_day"]["used"] == 25.0
    assert win["seven_day_fable"] == {
        "used": 49.0, "resets_at": win["seven_day"]["resets_at"],
        "label": "Fable", "active": True}
    assert "seven_day_oauth_apps" not in win      # null buckets are not windows


def test_windows_falls_back_to_legacy_buckets():
    win = claude.windows(LEGACY)
    assert win["five_hour"]["used"] == 13.0
    assert win["seven_day_sonnet"] == {"used": 7.0, "resets_at": None,
                                       "label": "sonnet", "active": False}
    assert "seven_day_opus" not in win


def test_get_claude_usage_exposes_the_binding_scoped_row(monkeypatch):
    claude._plan_cache = "MAX 5x"
    monkeypatch.setattr(claude, "_get_token", lambda: ("tok", {}, False))
    monkeypatch.setattr(claude, "_api_get", lambda path, token: FakeResp(200, LIVE))
    out = claude.get_claude_usage()
    assert out["ok"] and out["session_used"] == 21.0 and out["week_used"] == 25.0
    assert out["scoped"][0]["label"] == "Fable" and out["scoped"][0]["used"] == 49.0
    assert "Fable 49%" in claude.summary(out)
    assert claude.render(out).size == (128, 128)


def test_metrics_one_gauge_per_window(monkeypatch):
    claude._plan_cache = "MAX 5x"
    monkeypatch.setattr(claude, "_get_token", lambda: ("tok", {}, False))
    monkeypatch.setattr(claude, "_api_get", lambda path, token: FakeResp(200, LIVE))
    out = claude.metrics(claude.get_claude_usage())
    pct = {m[2]["window"]: m[1] for m in out if m[0] == "ai_plan_used_percent"}
    assert pct == {"five_hour": 21.0, "seven_day": 25.0, "seven_day_fable": 49.0}
    resets = {m[2]["window"]: m[1] for m in out if m[0] == "ai_plan_resets_at_seconds"}
    assert resets["seven_day"] == 1789009199.0          # 2026-09-10T02:59:59Z
    fable = next(m[2] for m in out if m[2]["window"] == "seven_day_fable")
    assert fable == {"vendor": "claude", "window": "seven_day_fable",
                     "scope": "Fable", "plan": "MAX 5x"}
    assert claude.metrics({"ok": False}) == []
