"""Claude usage widget: session (5-hour) + weekly (7-day) limits.

Consumer Claude plans (Pro / Max) have no Admin/API-key usage endpoint. The
data shown by Claude Code's `/usage` command comes from an OAuth-authenticated
endpoint that we can call directly using the access token Claude Code already
stores on disk:

    GET https://api.anthropic.com/api/oauth/usage
        Authorization: Bearer <claudeAiOauth.accessToken>
        anthropic-beta: oauth-2025-04-20

It returns rolling-window *utilization* percentages. Two shapes exist:

* `limits`: a list of {kind, percent, resets_at, scope} — kind `session` (the
  5-hour window), `weekly_all` (7 days, every model) and `weekly_scoped` (7
  days, one model, `scope.model.display_name`; this is the "Fable 49%" row
  the Usage page shows). Read 2026-09-07.
* the older top-level `five_hour` / `seven_day` / `seven_day_<model>` buckets,
  each {utilization, resets_at}. Still present, and the fallback here.

"Remaining" = 100 - utilization.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

import requests
from PIL import Image, ImageDraw

from .. import config
from ..render_kit import (
    BG, CLAUDE_BRAND, DIM, GRAY, SIZE, Fonts, header, usage_row, usage_row_compact,
    wrap_text,
)

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
        "User-Agent": "divoom-widgets/0.3",
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


def _get_token() -> tuple[Optional[str], dict, bool]:
    """Return (token, creds, dead). `dead` means the token is expired and could
    not be refreshed, so calling the API with it is pointless."""
    # Prefer an explicit long-lived token (e.g. from `claude setup-token`); this
    # path needs no credentials file and is used for headless/Docker deploys.
    if config.CLAUDE_OAUTH_TOKEN:
        return config.CLAUDE_OAUTH_TOKEN, {}, False
    creds = _read_creds()
    oauth = creds.get("claudeAiOauth", {})
    token = oauth.get("accessToken")
    expires_at = oauth.get("expiresAt", 0)
    now_ms = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
    # Refresh proactively if expired (or within 2 minutes of expiry).
    if token and expires_at and now_ms > expires_at - 120_000:
        refreshed = _refresh_token(creds)
        if not refreshed:
            # A copied credentials file shares one refresh token with the
            # machine it came from; once that machine rotates it, this copy can
            # never refresh again. Retrying the API with the expired token just
            # earns a rate limit, so report it instead of asking.
            return token, creds, True
        token = refreshed
    return token, creds, False


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


def windows(data: dict) -> dict:
    """Every usage window in the response, keyed the way the metrics store
    will see them: {key: {"used", "resets_at", "label", "active"}}.

    `limits` is read first (it is what the Usage page shows, model-scoped rows
    included); the legacy top-level buckets fill in anything it lacks.
    """
    out: dict = {}
    for lim in data.get("limits") or []:
        if not isinstance(lim, dict) or lim.get("percent") is None:
            continue
        kind = lim.get("kind")
        scope = ((lim.get("scope") or {}).get("model") or {})
        model = scope.get("display_name") or scope.get("id") or ""
        if kind == "session":
            key, label = "five_hour", "session"
        elif kind == "weekly_all":
            key, label = "seven_day", "all models"
        elif kind == "weekly_scoped" and model:
            key, label = f"seven_day_{model.lower().replace(' ', '_')}", model
        else:
            continue
        out[key] = {"used": float(lim["percent"]), "resets_at": _parse_dt(lim.get("resets_at")),
                    "label": label, "active": bool(lim.get("is_active"))}
    legacy_labels = {"five_hour": "session", "seven_day": "all models"}
    for key, bucket in data.items():
        if key in out or not key.startswith(("five_hour", "seven_day")):
            continue
        used = _util(bucket)
        if used is None:
            continue
        label = legacy_labels.get(key, key.replace("seven_day_", "").replace("_", " "))
        out[key] = {"used": used, "resets_at": _parse_dt(bucket.get("resets_at")),
                    "label": label, "active": False}
    return out


def get_claude_usage() -> dict:
    """Return normalized Claude usage, or {'ok': False, 'error': ...}."""
    try:
        token, creds, dead = _get_token()
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"creds: {exc}"}
    if not token:
        return {"ok": False, "error": "no access token"}
    if dead:
        # Kept short so it fits the 128px card; summary() carries the remedy.
        return {"ok": False, "error": "auth expired"}

    try:
        resp = _api_get("/api/oauth/usage", token)
        if resp.status_code == 401:
            token = _refresh_token(creds) or token
            resp = _api_get("/api/oauth/usage", token)
        if resp.status_code == 403:
            # `claude setup-token` mints an inference-scoped token; this
            # endpoint requires user:profile, which only a full Claude Code
            # login has. Worth naming, because the 403 body is not shown.
            return {"ok": False, "error": "token scope"}
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    win = windows(data)
    session = win.get("five_hour") or {}
    week = win.get("seven_day") or {}
    # Model-scoped weekly rows (e.g. "Fable 49%"): the binding one first.
    scoped = sorted(
        (dict(v, key=k) for k, v in win.items()
         if k.startswith("seven_day_") and k not in ("seven_day_oauth_apps",)),
        key=lambda v: (not v["active"], -v["used"]))
    return {
        "ok": True,
        "plan": _plan_label(token),
        "week_used": week.get("used", 0.0),
        "week_resets_at": week.get("resets_at"),
        "session_used": session.get("used", 0.0),
        "session_resets_at": session.get("resets_at"),
        "opus_used": (win.get("seven_day_opus") or {}).get("used"),
        "sonnet_used": (win.get("seven_day_sonnet") or {}).get("used"),
        "scoped": scoped,
        "windows": win,
    }


# --- Widget interface ------------------------------------------------------

def fetch() -> dict:
    return get_claude_usage()


def interval_s() -> int:
    return config.UPDATE_INTERVAL_SECONDS


def summary(data: dict) -> str:
    if data.get("ok"):
        text = f"CLD session {data['session_used']:.0f}% / weekly {data['week_used']:.0f}%"
        if data.get("scoped"):
            s = data["scoped"][0]
            text += f" / {s['label']} {s['used']:.0f}%"
        return text
    error = data.get("error")
    if error == "auth expired":
        return ("CLD err:auth expired — these credentials can no longer refresh; "
                "supply a credentials file from its own Claude Code login")
    if error == "token scope":
        return ("CLD err:token scope — the usage endpoint needs user:profile, "
                "which a `claude setup-token` token does not carry; use a "
                "credentials file from a full Claude Code login instead")
    return f"CLD err:{error}"


def metrics(data: dict) -> list:
    """Gauges for the optional metrics store: one per usage window."""
    if not data.get("ok"):
        return []
    out = []
    for key, w in (data.get("windows") or {}).items():
        attrs = {"vendor": "claude", "window": key, "scope": w.get("label", ""),
                 "plan": data.get("plan", "")}
        out.append(("ai_plan_used_percent", w["used"], attrs))
        if w.get("resets_at"):
            out.append(("ai_plan_resets_at_seconds", w["resets_at"].timestamp(), attrs))
    return out


def render(data: dict) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    draw = ImageDraw.Draw(img)
    fonts = Fonts()

    header(draw, fonts, "CLAUDE", CLAUDE_BRAND,
           data.get("plan", "") if data.get("ok") else "")

    if not data.get("ok"):
        draw.text((5, 52), "no data", font=fonts.title, fill=GRAY)
        for i, line in enumerate(wrap_text(draw, data.get("error", ""), fonts.tiny, SIZE - 10)):
            draw.text((5, 70 + i * 11), line, font=fonts.tiny, fill=DIM)
        return img

    scoped = data.get("scoped") or []
    if not scoped:
        # Two windows: the original two-row card.
        usage_row(draw, fonts, 28, "SESSION", "5h",
                  data.get("session_used", 0.0), data.get("session_resets_at"))
        usage_row(draw, fonts, 78, "WEEKLY", "7d",
                  data.get("week_used", 0.0), data.get("week_resets_at"))
        return img

    # Three windows: session, weekly (all models) and the binding model row —
    # the same three bars the Usage page shows, in compact form.
    s = scoped[0]
    usage_row_compact(draw, fonts, 26, "SESSION", "5h",
                      data.get("session_used", 0.0), data.get("session_resets_at"))
    usage_row_compact(draw, fonts, 60, "WEEKLY", "7d all",
                      data.get("week_used", 0.0), data.get("week_resets_at"))
    usage_row_compact(draw, fonts, 94, s["label"].upper()[:9], "7d model",
                      s["used"], s.get("resets_at"))
    return img
