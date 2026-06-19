"""Environment-based configuration for the usage widget."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional; env vars still work without it
    pass


def _int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None else v.strip()


# --- Device ---------------------------------------------------------------
DIVOOM_HOST = _str("DIVOOM_HOST", "192.168.1.50")
# Which of the five LCD panels to draw on (1 = leftmost ... 5 = rightmost).
WIDGET_PANEL = max(1, min(5, _int("WIDGET_PANEL", 1)))
UPDATE_INTERVAL_SECONDS = max(15, _int("UPDATE_INTERVAL_SECONDS", 300))

# --- Claude (Max/Pro consumer plan via Claude Code OAuth token) -----------
# The widget reads the OAuth access token that Claude Code stores locally and
# calls the same usage endpoint the `/usage` command uses. No API/Admin key.
CLAUDE_CREDENTIALS_PATH = _str(
    "CLAUDE_CREDENTIALS_PATH",
    str(Path.home() / ".claude" / ".credentials.json"),
)
# Public OAuth client id for the "Claude Code" application (used for refresh).
CLAUDE_OAUTH_CLIENT_ID = _str(
    "CLAUDE_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
)
# Optional long-lived OAuth token from `claude setup-token` (sk-ant-oat01-...).
# When set, it is used directly instead of reading the credentials file — handy
# for headless / Docker deploys where Claude Code is not installed.
CLAUDE_OAUTH_TOKEN = _str("CLAUDE_CODE_OAUTH_TOKEN")

# --- GitHub Copilot (individual Pro) --------------------------------------
GITHUB_OAUTH_TOKEN = _str("GITHUB_OAUTH_TOKEN")
GITHUB_USERNAME = _str("GITHUB_USERNAME")
# Fallback monthly premium-request allotment for Copilot Pro when the API
# does not return an explicit included amount (Pro = 300 legacy premium reqs).
COPILOT_MONTHLY_QUOTA = _int("COPILOT_MONTHLY_QUOTA", 300)
