import datetime as dt

from divoom_widgets.widgets import claude_usage as claude


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
    monkeypatch.setattr(claude, "_get_token", lambda: ("tok", {}, False))
    monkeypatch.setattr(claude, "_api_get", _fake_api_get)
    out = claude.get_claude_usage()
    assert out["ok"] is True
    assert out["session_used"] == 13.0
    assert out["week_used"] == 2.0
    assert out["plan"] == "MAX 5x"
    assert out["session_resets_at"].year == 2026


def test_get_claude_usage_http_error(monkeypatch):
    monkeypatch.setattr(claude, "_get_token", lambda: ("tok", {}, False))
    monkeypatch.setattr(claude, "_api_get", lambda path, token: FakeResp(500, {}))
    out = claude.get_claude_usage()
    assert out["ok"] is False
    assert "500" in out["error"]


def test_get_claude_usage_no_token(monkeypatch):
    monkeypatch.setattr(claude, "_get_token", lambda: (None, {}, False))
    out = claude.get_claude_usage()
    assert out["ok"] is False


def test_get_token_prefers_env(monkeypatch):
    monkeypatch.setattr(claude.config, "CLAUDE_OAUTH_TOKEN", "sk-ant-oat01-env")
    token, creds, dead = claude._get_token()
    assert token == "sk-ant-oat01-env"
    assert creds == {}
    assert dead is False


def test_expired_token_that_cannot_refresh_is_reported_not_retried(monkeypatch):
    """A copied credentials file shares one refresh token with its origin; once
    that rotates, this copy is dead. Calling the API anyway earned a 429."""
    expired = (dt.datetime.now(dt.timezone.utc).timestamp() - 3600) * 1000
    monkeypatch.setattr(claude.config, "CLAUDE_OAUTH_TOKEN", "")
    monkeypatch.setattr(claude, "_read_creds", lambda: {
        "claudeAiOauth": {"accessToken": "stale", "refreshToken": "rotated-away",
                          "expiresAt": expired}})
    monkeypatch.setattr(claude, "_refresh_token", lambda creds: None)

    token, _creds, dead = claude._get_token()
    assert dead is True

    called = []
    monkeypatch.setattr(claude, "_api_get",
                        lambda path, tok: called.append(path) or FakeResp(429, {}))
    out = claude.get_claude_usage()
    assert out["ok"] is False
    assert "auth expired" in out["error"]
    assert called == [], "must not call the API with a token known to be dead"


def test_403_is_reported_as_a_scope_problem(monkeypatch):
    """`claude setup-token` tokens are inference-scoped; the usage endpoint
    needs user:profile and answers 403. Name it, the body is never shown."""
    monkeypatch.setattr(claude, "_get_token", lambda: ("tok", {}, False))
    monkeypatch.setattr(claude, "_api_get", lambda path, token: FakeResp(403, {}))
    out = claude.get_claude_usage()
    assert out["ok"] is False
    assert out["error"] == "token scope"
    assert "user:profile" in claude.summary(out)
