"""GitHub Copilot (individual Pro) usage provider.

As of 2026, GitHub exposes per-user billing usage under the enhanced billing
platform:

    GET /users/{username}/settings/billing/premium_request/usage   (legacy)
    GET /users/{username}/settings/billing/ai_credit/usage         (usage-based)

These require a token with the user "Plan" permission. A GitHub App
user-to-server OAuth token (gho_/ghu_) works; classic and fine-grained PATs
historically do not for the personal billing scope. App user tokens expire
(default 8h), so a 401 here usually means the token must be re-minted
(see scripts/get-github-token.mjs). The widget degrades gracefully in that case.
"""
from __future__ import annotations

from typing import Any, Optional

import requests

from . import config

API = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "divoom-widgets/0.2",
    }


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _extract(data: Any) -> tuple[float, Optional[float]]:
    """Best-effort (used, limit) extraction across known response shapes."""
    if not isinstance(data, dict):
        return 0.0, None

    items = data.get("usageItems") or data.get("usage_items") or []
    used = 0.0
    for it in items:
        if not isinstance(it, dict):
            continue
        for key in ("totalQuantity", "total_quantity_used", "grossQuantity", "quantity", "netAmount"):
            if key in it:
                used += _num(it[key])
                break

    # Some shapes report a single total instead of line items.
    for key in ("totalQuantity", "total_quantity_used", "totalUsage", "used"):
        if used == 0.0 and key in data:
            used = _num(data[key])

    limit = None
    for key in ("included", "included_quantity", "totalIncluded", "monthly_quota", "limit", "allowance"):
        if key in data and data[key] is not None:
            limit = _num(data[key])
            break
    return used, limit


def get_copilot_usage() -> dict:
    token = config.GITHUB_OAUTH_TOKEN
    user = config.GITHUB_USERNAME
    if not token or not user:
        return {"ok": False, "error": "no token"}

    endpoints = [
        ("ai_credit", f"/users/{user}/settings/billing/ai_credit/usage"),
        ("premium", f"/users/{user}/settings/billing/premium_request/usage"),
    ]
    last_err = "unavailable"
    for source, path in endpoints:
        try:
            resp = requests.get(f"{API}{path}", headers=_headers(token), timeout=15)
        except requests.RequestException as exc:
            last_err = str(exc)
            continue
        if resp.status_code in (401, 403):
            return {"ok": False, "error": "auth"}
        if resp.status_code != 200:
            last_err = f"HTTP {resp.status_code}"
            continue
        try:
            data = resp.json()
        except ValueError:
            last_err = "bad json"
            continue
        used, limit = _extract(data)
        if limit is None and config.COPILOT_MONTHLY_QUOTA > 0:
            limit = float(config.COPILOT_MONTHLY_QUOTA)
        return {"ok": True, "used": used, "limit": limit, "source": source}

    return {"ok": False, "error": last_err}
