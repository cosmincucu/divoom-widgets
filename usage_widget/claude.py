"""Claude usage provider.

Consumer Claude plans (Pro / Max) have no Admin/API-key usage endpoint. The
data shown by Claude Code's `/usage` command comes from an OAuth-authenticated
endpoint that we can call directly using the access token Claude Code already
stores on disk:

    GET https://api.anthropic.com/api/oauth/usage
        Authorization: Bearer <claudeAiOauth.accessToken>
        anthropic-beta: oauth-2025-04-20

It returns rolling-window *utilization* percentages (5-hour session and 7-day
windows, optionally per-model). "Remaining" = 100 - utilization.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

import requests

from . import config

API_BASE = "https://api.anthropic.com"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
BETA = "oauth-2025-04-20"

_PLAN_LABELS = {
    "default_claude_max_20x": "MAX 20x",
    "default_claude_max_5x": "MAX 5x",
    "default_claude_max": "MAX",
    "default_claude_pro": "PRO",
    "default_claude_ai": "FREE",
}

_plan_cache: Optional[str] = None


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": BETA,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "User-Agent": "divoom-widgets/0.2",
    }


def _read_creds() -> dict:
    path = Path(config.CLAUDE_CREDENTIALS_PATH)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_creds(data: dict) -> None:
    """Atomically write the credentials file back, preserving structure."""
    path = Path(config.CLAUDE_CREDENTIALS_PATH)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _refresh_token(creds: dict) -> Optional[str]:
    """Use the refresh token to mint a new access token; persist it."""
    oauth = creds.get("claudeAiOauth", {})
    refresh = oauth.get("refreshToken")
    if not refresh:
        return None
    try:
        resp = requests.post(
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": config.CLAUDE_OAUTH_CLIENT_ID,
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
    except (requests.RequestException, ValueError):
        return None

    access = body.get("access_token")
    if not access:
        return None
    oauth["accessToken"] = access
    if body.get("refresh_token"):
        oauth["refreshToken"] = body["refresh_token"]
    if body.get("expires_in"):
        oauth["expiresAt"] = int(
            (dt.datetime.now(dt.timezone.utc).timestamp() + body["expires_in"]) * 1000
        )
    creds["claudeAiOauth"] = oauth
    try:
        _write_creds(creds)
    except OSError:
        pass  # in-memory token still usable this cycle
    return access


def _get_token() -> tuple[Optional[str], dict]:
    # Prefer an explicit long-lived token (e.g. from `claude setup-token`); this
    # path needs no credentials file and is used for headless/Docker deploys.
    if config.CLAUDE_OAUTH_TOKEN:
        return config.CLAUDE_OAUTH_TOKEN, {}
    creds = _read_creds()
    oauth = creds.get("claudeAiOauth", {})
    token = oauth.get("accessToken")
    expires_at = oauth.get("expiresAt", 0)
    now_ms = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
    # Refresh proactively if expired (or within 2 minutes of expiry).
    if token and expires_at and now_ms > expires_at - 120_000:
        token = _refresh_token(creds) or token
    return token, creds


def _api_get(path: str, token: str) -> requests.Response:
    return requests.get(f"{API_BASE}{path}", headers=_headers(token), timeout=15)


def _plan_label(token: str) -> str:
    global _plan_cache
    if _plan_cache is not None:
        return _plan_cache
    try:
        resp = _api_get("/api/oauth/profile", token)
        if resp.status_code == 200:
            org = resp.json().get("organization", {})
            tier = org.get("rate_limit_tier", "")
            _plan_cache = _PLAN_LABELS.get(tier, (org.get("organization_type") or "CLAUDE").upper())
            return _plan_cache
    except (requests.RequestException, ValueError):
        pass
    _plan_cache = "CLAUDE"
    return _plan_cache


def _parse_dt(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _util(bucket: Any) -> Optional[float]:
    if isinstance(bucket, dict) and bucket.get("utilization") is not None:
        return float(bucket["utilization"])
    return None


def get_claude_usage() -> dict:
    """Return normalized Claude usage, or {'ok': False, 'error': ...}."""
    try:
        token, creds = _get_token()
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"creds: {exc}"}
    if not token:
        return {"ok": False, "error": "no access token"}

    try:
        resp = _api_get("/api/oauth/usage", token)
        if resp.status_code == 401:
            token = _refresh_token(creds) or token
            resp = _api_get("/api/oauth/usage", token)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "plan": _plan_label(token),
        "week_used": _util(data.get("seven_day")) or 0.0,
        "week_resets_at": _parse_dt((data.get("seven_day") or {}).get("resets_at")),
        "session_used": _util(data.get("five_hour")) or 0.0,
        "session_resets_at": _parse_dt((data.get("five_hour") or {}).get("resets_at")),
        "opus_used": _util(data.get("seven_day_opus")),
        "sonnet_used": _util(data.get("seven_day_sonnet")),
    }
