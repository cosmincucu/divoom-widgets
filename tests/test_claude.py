import datetime as dt

from usage_widget import claude


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


USAGE = {
    "five_hour": {"utilization": 13.0, "resets_at": "2026-06-19T01:10:00+00:00"},
    "seven_day": {"utilization": 2.0, "resets_at": "2026-06-25T03:00:00+00:00"},
    "seven_day_opus": None,
    "seven_day_sonnet": {"utilization": 0.0, "resets_at": None},
}
PROFILE = {"organization": {"rate_limit_tier": "default_claude_max_5x"}}


def test_parse_dt():
    assert claude._parse_dt("2026-06-19T01:10:00Z").year == 2026
    assert claude._parse_dt("not-a-date") is None
    assert claude._parse_dt(None) is None


def test_util():
    assert claude._util({"utilization": 5.0}) == 5.0
    assert claude._util({"utilization": None}) is None
    assert claude._util(None) is None


def _fake_api_get(path, token):
    if "usage" in path:
        return FakeResp(200, USAGE)
    if "profile" in path:
        return FakeResp(200, PROFILE)
    return FakeResp(404, {})


def test_get_claude_usage_success(monkeypatch):
    claude._plan_cache = None
    monkeypatch.setattr(claude, "_get_token", lambda: ("tok", {}))
    monkeypatch.setattr(claude, "_api_get", _fake_api_get)
    out = claude.get_claude_usage()
    assert out["ok"] is True
    assert out["session_used"] == 13.0
    assert out["week_used"] == 2.0
    assert out["plan"] == "MAX 5x"
    assert out["session_resets_at"].year == 2026


def test_get_claude_usage_http_error(monkeypatch):
    monkeypatch.setattr(claude, "_get_token", lambda: ("tok", {}))
    monkeypatch.setattr(claude, "_api_get", lambda path, token: FakeResp(500, {}))
    out = claude.get_claude_usage()
    assert out["ok"] is False
    assert "500" in out["error"]


def test_get_claude_usage_no_token(monkeypatch):
    monkeypatch.setattr(claude, "_get_token", lambda: (None, {}))
    out = claude.get_claude_usage()
    assert out["ok"] is False


def test_get_token_prefers_env(monkeypatch):
    monkeypatch.setattr(claude.config, "CLAUDE_OAUTH_TOKEN", "sk-ant-oat01-env")
    token, creds = claude._get_token()
    assert token == "sk-ant-oat01-env"
    assert creds == {}
