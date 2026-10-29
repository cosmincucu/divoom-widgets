"""Codex plan usage read live from ChatGPT, the way the Codex CLI's status screen does.

Codex does have a usage endpoint — it is the one the CLI itself calls, with the
same ChatGPT login it keeps in `auth.json`:

    GET https://chatgpt.com/backend-api/wham/usage
        Authorization: Bearer <tokens.access_token>
        ChatGPT-Account-Id: <tokens.account_id>

    {"plan_type": "plus",
     "rate_limit": {"primary_window":   {"used_percent", "limit_window_seconds",
                                         "reset_after_seconds", "reset_at"},
                    "secondary_window": {... or absent}},
     "credits": {...}, ...}                                   (illustrative shape)

That answer is the plan's standing NOW, idle or not, on every host, including
Codex on the web and the phone — which the transcript route in codex_usage.py
can never be: a transcript carries the figure as of the last turn, so the card
can keep showing a stale percentage of a 5-hour window long after that window
has emptied.

The access token is a JWT that lives ten days (iat/exp claims, ten days
apart). The widget refreshes it the way the CLI does —
`POST https://auth.openai.com/oauth/token` with the CLI's public client id and
the refresh token — and writes the rotated tokens back, so the file must be a
login made FOR the widget (`CODEX_HOME=<its own dir> codex login`): a copy of a
login still used elsewhere shares one refresh token, and the first rotation on
either side breaks the other.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import math
import os
import tempfile
from typing import Any, Optional

import requests

from .. import config

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
TOKEN_URL = "https://auth.openai.com/oauth/token"
# What the CLI asks for when it refreshes; offline_access is what makes the
# answer carry a refresh token again.
REFRESH_SCOPE = "openid profile email offline_access"
# Refresh this close to the access token's end rather than on the 401.
REFRESH_MARGIN_S = 120
RELOGIN_HINT = ("log the widget's own Codex account in again (CODEX_HOME=<its own dir> "
                "codex login) and replace the mounted auth.json")

PLAN_LABELS = {
    "prolite": "PRO LITE",
    "pro": "PRO",
    "plus": "PLUS",
    "team": "TEAM",
    "business": "BUSINESS",
    "enterprise": "ENTERPRISE",
    "free": "FREE",
}


def plan_label(plan: str) -> str:
    return PLAN_LABELS.get((plan or "").lower(), (plan or "").upper() or "CODEX")


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _epoch(value: Any) -> Optional[dt.datetime]:
    num = _number(value)
    if num is None or num <= 0:
        return None
    try:
        return dt.datetime.fromtimestamp(int(num), dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


# --- the login file ---------------------------------------------------------

def _read_auth() -> dict:
    with open(config.CODEX_AUTH_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("auth.json is not an object")
    return data


def _write_auth(data: dict) -> None:
    """Atomic replace next to the file (a bind-mounted FILE would refuse this;
    the directory is what gets mounted, as for the Claude credentials)."""
    path = config.CODEX_AUTH_PATH
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def jwt_claims(token: str) -> dict:
    """The payload of a JWT, unverified — only its public `exp` and account
    claims are read, and the usage endpoint is what verifies the token."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def token_expires_at(token: str) -> Optional[dt.datetime]:
    return _epoch(jwt_claims(token).get("exp"))


def _account_id(tokens: dict, access: str) -> str:
    """auth.json carries the account id; a login written without it still has
    it inside the access token, where the CLI reads it from too."""
    given = tokens.get("account_id")
    if given:
        return str(given)
    auth = jwt_claims(access).get("https://api.openai.com/auth") or {}
    return str(auth.get("chatgpt_account_id") or "")


def _refresh(auth: dict) -> Optional[str]:
    """Mint a new access token from the refresh token; persist the rotation."""
    tokens = auth.get("tokens") or {}
    refresh = tokens.get("refresh_token")
    if not refresh:
        return None
    try:
        resp = requests.post(
            TOKEN_URL,
            json={"client_id": config.CODEX_OAUTH_CLIENT_ID,
                  "grant_type": "refresh_token",
                  "refresh_token": refresh,
                  "scope": REFRESH_SCOPE},
            headers={"Content-Type": "application/json",
                     "User-Agent": "divoom-widgets/0.3"},
            timeout=15)
        if resp.status_code != 200:
            return None
        body = resp.json()
    except (requests.RequestException, ValueError):
        return None
    access = body.get("access_token") if isinstance(body, dict) else None
    if not access:
        return None
    tokens["access_token"] = access
    for key in ("refresh_token", "id_token"):
        if body.get(key):
            tokens[key] = body[key]
    auth["tokens"] = tokens
    auth["last_refresh"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        _write_auth(auth)
    except OSError:
        pass  # this cycle still has the token; the next one refreshes again
    return access


def _get_token() -> tuple[Optional[str], str, dict, bool]:
    """(access token, account id, auth, dead). `dead` = expired and the refresh
    was refused, so the usage call would only earn a 401."""
    auth = _read_auth()
    tokens = auth.get("tokens") or {}
    access = tokens.get("access_token")
    if not access:
        return None, "", auth, False
    expires = token_expires_at(access)
    now = dt.datetime.now(dt.timezone.utc)
    if expires is None or (expires - now).total_seconds() < REFRESH_MARGIN_S:
        refreshed = _refresh(auth)
        if refreshed:
            access = refreshed
        elif expires is not None and expires <= now:
            return access, _account_id(tokens, access), auth, True
    return access, _account_id(tokens, access), auth, False


# --- the endpoint -----------------------------------------------------------

def _api_get(token: str, account_id: str) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/json",
               "User-Agent": "divoom-widgets/0.3"}
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return requests.get(USAGE_URL, headers=headers, timeout=15)


def _window(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    used = _number(raw.get("used_percent"))
    if used is None or used < 0:
        return None
    length_s = _number(raw.get("limit_window_seconds"))
    minutes = int(length_s // 60) if length_s and length_s > 0 else 0
    reset_after = _number(raw.get("reset_after_seconds"))
    # An unused window has not started: the endpoint then reports the whole
    # window ahead, which is "no reset pending", not a countdown.
    idle = (used == 0 and reset_after is not None and length_s is not None
            and reset_after >= length_s)
    return {"used": used,
            "window_minutes": minutes,
            "resets_at": None if idle else _epoch(raw.get("reset_at")),
            "idle": idle}


def normalize(body: dict, now: Optional[dt.datetime] = None) -> dict:
    """The widget's data dict from one usage answer. Same keys as the
    transcript route produces, plus source="live"; no last turn (the endpoint
    does not say, and the figure does not depend on one any more)."""
    rate = body.get("rate_limit") if isinstance(body, dict) else None
    if not isinstance(rate, dict):
        return {"ok": False, "error": "no rate_limit in answer"}
    windows = [w for w in (_window(rate.get("primary_window")),
                           _window(rate.get("secondary_window"))) if w]
    if not windows:
        return {"ok": False, "error": "no usage window in answer"}
    # Shortest first (SESSION above WEEKLY); unknown length last.
    windows.sort(key=lambda w: w["window_minutes"] or 10**9)
    week = next((w for w in windows if w["window_minutes"] == 10080), windows[-1])
    plan = str(body.get("plan_type") or "")
    return {
        "ok": True,
        "source": "live",
        "fetched_at": now or dt.datetime.now(dt.timezone.utc),
        "plan": plan_label(plan),
        "plan_raw": plan,
        "windows": windows,
        "week_used": week["used"],
        "week_resets_at": week["resets_at"],
        "window_minutes": week["window_minutes"],
        "last_turn_at": None,
    }


def get_usage() -> dict:
    path = config.CODEX_AUTH_PATH
    if not os.path.exists(path):
        return {"ok": False, "error": "no codex login", "relogin": True}
    try:
        token, account_id, auth, dead = _get_token()
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"auth.json: {exc}"[:60]}
    if not token:
        return {"ok": False, "error": "no access token", "relogin": True}
    if dead:
        return {"ok": False, "error": "auth expired", "relogin": True}
    try:
        resp = _api_get(token, account_id)
        if resp.status_code == 401:
            token = _refresh(auth) or token
            resp = _api_get(token, account_id)
        if resp.status_code == 401:
            return {"ok": False, "error": "auth expired", "relogin": True}
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        body = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:60]}
    return normalize(body)
