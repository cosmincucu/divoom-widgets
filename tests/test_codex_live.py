"""The live Codex route: the usage endpoint, the login file and its refresh."""
import base64
import datetime as dt
import json
import os
import time

import pytest

from divoom_widgets.widgets import codex_live as live
from divoom_widgets.widgets import codex_usage as codex

UTC = dt.timezone.utc


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.sig"


def _auth(access_exp: float, account="acct_1", refresh="rt_1") -> dict:
    claims = {"exp": access_exp, "https://api.openai.com/auth": {"chatgpt_account_id": account}}
    return {"auth_mode": "chatgpt", "last_refresh": "2026-10-19T20:24:37Z",
            "tokens": {"id_token": "id", "access_token": _jwt(claims),
                       "refresh_token": refresh, "account_id": account}}


# A Plus account that has not run Codex for a while (illustrative values): both
# windows unused, each reporting the whole window ahead.
PLUS_IDLE = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {"used_percent": 0, "limit_window_seconds": 18000,
                           "reset_after_seconds": 18000, "reset_at": 1793290055},
        "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800,
                             "reset_after_seconds": 604800, "reset_at": 1793876855}},
    "credits": {"has_credits": True, "unlimited": False, "balance": "41816.14"},
}
# The same plan mid-use.
PLUS_BUSY = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {"used_percent": 49, "limit_window_seconds": 18000,
                           "reset_after_seconds": 11000, "reset_at": 1793163191},
        "secondary_window": {"used_percent": 15, "limit_window_seconds": 604800,
                             "reset_after_seconds": 500000, "reset_at": 1793652828}},
}
# A Pro plan: one weekly window, no 5-hour one.
PRO = {"plan_type": "pro",
       "rate_limit": {"primary_window": {"used_percent": 63, "limit_window_seconds": 604800,
                                         "reset_after_seconds": 300000, "reset_at": 1793652828}}}


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.fixture
def auth_file(monkeypatch, tmp_path):
    path = tmp_path / "codex" / "auth.json"
    path.parent.mkdir()
    monkeypatch.setattr(live.config, "CODEX_AUTH_PATH", str(path))
    monkeypatch.setattr(codex.config, "CODEX_AUTH_PATH", str(path))

    def write(data):
        path.write_text(json.dumps(data), encoding="utf-8")
        return path
    return write


def test_normalize_plus_idle_has_two_unstarted_windows():
    out = live.normalize(PLUS_IDLE, now=dt.datetime(2026, 10, 29, 11, 6, tzinfo=UTC))
    assert out["ok"] is True and out["source"] == "live" and out["plan"] == "PLUS"
    assert [(w["window_minutes"], w["used"], w["idle"]) for w in out["windows"]] == \
        [(300, 0.0, True), (10080, 0.0, True)]
    # An unstarted window has no reset pending, so no countdown is drawn.
    assert all(w["resets_at"] is None for w in out["windows"])
    assert out["week_used"] == 0.0 and out["last_turn_at"] is None
    assert codex.summary(out) == "CDX session 0% / weekly 0% (PLUS, live)"


def test_normalize_plus_busy_keeps_resets():
    out = live.normalize(PLUS_BUSY)
    assert [w["used"] for w in out["windows"]] == [49.0, 15.0]
    assert out["windows"][0]["resets_at"] == dt.datetime.fromtimestamp(1793163191, UTC)
    assert out["windows"][0]["idle"] is False
    assert out["week_resets_at"] == dt.datetime.fromtimestamp(1793652828, UTC)
    pct = {m[2]["window"]: m[1] for m in codex.metrics(out) if m[0] == "ai_plan_used_percent"}
    assert pct == {"5h": 49.0, "7d": 15.0}
    # No last-turn gauge: the live route has no turn to report.
    assert not [m for m in codex.metrics(out) if m[0] == "ai_plan_last_activity_seconds"]


def test_normalize_pro_is_one_weekly_window():
    out = live.normalize(PRO)
    assert out["plan"] == "PRO" and len(out["windows"]) == 1
    assert out["windows"][0]["window_minutes"] == 10080 and out["week_used"] == 63.0
    assert codex.summary(out) == "CDX weekly 63% (PRO, live)"


def test_normalize_rejects_an_answer_without_usage():
    assert live.normalize({})["ok"] is False
    assert live.normalize({"rate_limit": {}})["ok"] is False
    assert live.normalize({"rate_limit": {"primary_window": {"used_percent": -1}}})["ok"] is False
    bad = {"rate_limit": {"primary_window": {"used_percent": "nan", "limit_window_seconds": 1}}}
    assert live.normalize(bad)["ok"] is False
    assert codex.metrics(live.normalize(bad)) == []


def test_render_pro_and_plus_screens_are_128px():
    assert codex.render(live.normalize(PRO)).size == (128, 128)
    assert codex.render(live.normalize(PLUS_IDLE)).size == (128, 128)
    assert codex.render(live.normalize(PLUS_BUSY)).size == (128, 128)
    assert codex.render({"ok": False, "error": "auth expired", "relogin": True}).size == (128, 128)


def test_get_usage_reads_the_endpoint_with_the_login(auth_file, monkeypatch):
    auth_file(_auth(time.time() + 86400))
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        return FakeResp(200, PLUS_BUSY)
    monkeypatch.setattr(live.requests, "get", fake_get)
    monkeypatch.setattr(live.requests, "post", lambda *a, **k: pytest.fail("no refresh needed"))
    out = live.get_usage()
    assert out["ok"] is True and out["week_used"] == 15.0
    url, headers = calls[0]
    assert url == live.USAGE_URL
    assert headers["ChatGPT-Account-Id"] == "acct_1"
    assert headers["Authorization"].startswith("Bearer eyJ")


def test_account_id_falls_back_to_the_token_claim():
    tokens = {"access_token": _auth(1, account="acct_claim")["tokens"]["access_token"]}
    assert live._account_id(tokens, tokens["access_token"]) == "acct_claim"
    assert live._account_id({"account_id": "given"}, "x.y.z") == "given"


def test_expiring_token_is_refreshed_and_the_rotation_persisted(auth_file, monkeypatch):
    path = auth_file(_auth(time.time() + 30))    # inside the refresh margin
    posts = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append((url, json))
        return FakeResp(200, {"access_token": _jwt({"exp": time.time() + 864000}),
                              "refresh_token": "rt_2", "id_token": "id_2"})
    monkeypatch.setattr(live.requests, "post", fake_post)
    monkeypatch.setattr(live.requests, "get", lambda url, headers=None, timeout=None:
                        FakeResp(200, PLUS_IDLE))
    assert live.get_usage()["ok"] is True
    url, body = posts[0]
    assert url == live.TOKEN_URL
    assert body["grant_type"] == "refresh_token" and body["refresh_token"] == "rt_1"
    assert body["client_id"] == live.config.CODEX_OAUTH_CLIENT_ID
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["tokens"]["refresh_token"] == "rt_2" and saved["tokens"]["id_token"] == "id_2"
    assert saved["tokens"]["account_id"] == "acct_1"          # untouched fields survive
    assert saved["last_refresh"].endswith("Z")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert not list(path.parent.glob("*.tmp"))


def test_a_401_triggers_one_refresh_then_reports(auth_file, monkeypatch):
    auth_file(_auth(time.time() + 86400))
    answers = [FakeResp(401), FakeResp(200, PRO)]
    monkeypatch.setattr(live.requests, "get", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(live.requests, "post", lambda *a, **k:
                        FakeResp(200, {"access_token": _jwt({"exp": time.time() + 864000})}))
    assert live.get_usage()["plan"] == "PRO"
    # Still 401 after the refresh: the login is dead, and the card says what to do.
    answers[:] = [FakeResp(401), FakeResp(401)]
    out = live.get_usage()
    assert out == {"ok": False, "error": "auth expired", "relogin": True}
    assert "codex login" in codex.summary(out)


def test_expired_token_that_cannot_refresh_is_dead_without_calling_usage(auth_file, monkeypatch):
    auth_file(_auth(time.time() - 10))
    monkeypatch.setattr(live.requests, "post", lambda *a, **k: FakeResp(400, {"error": "invalid_grant"}))
    monkeypatch.setattr(live.requests, "get", lambda *a, **k: pytest.fail("usage called with a dead token"))
    out = live.get_usage()
    assert out["ok"] is False and out["error"] == "auth expired" and out["relogin"]


def test_missing_or_broken_login_file(auth_file, monkeypatch):
    out = live.get_usage()
    assert out == {"ok": False, "error": "no codex login", "relogin": True}
    assert "codex login" in codex.summary(out)
    path = auth_file({"tokens": {}})
    assert live.get_usage()["error"] == "no access token"
    path.write_text("{not json", encoding="utf-8")
    out = live.get_usage()
    assert out["ok"] is False and out["error"].startswith("auth.json:")


def test_http_and_network_errors_are_reported_not_hidden(auth_file, monkeypatch):
    auth_file(_auth(time.time() + 86400))
    monkeypatch.setattr(live.requests, "get", lambda *a, **k: FakeResp(503))
    assert live.get_usage() == {"ok": False, "error": "HTTP 503"}

    def boom(*a, **k):
        raise live.requests.ConnectionError("dns")
    monkeypatch.setattr(live.requests, "get", boom)
    assert live.get_usage()["error"] == "dns"


def test_jwt_helpers_tolerate_garbage():
    assert live.jwt_claims("not-a-jwt") == {}
    assert live.jwt_claims("a.!!!.c") == {}
    assert live.token_expires_at("a.b.c") is None
    assert live.token_expires_at(_jwt({"exp": 1793290055})) == dt.datetime.fromtimestamp(1793290055, UTC)
