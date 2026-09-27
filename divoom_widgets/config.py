"""Environment-based configuration for the widget framework."""
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


def secret(value: str, path: str) -> str:
    """A secret given directly, or as the first non-empty line of a file
    (mounted secrets). The direct value wins when both are set."""
    if value:
        return value
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        return line
        except OSError:
            return ""
    return ""


# --- Device ---------------------------------------------------------------
DIVOOM_HOST = _str("DIVOOM_HOST", "192.168.1.50")
# Which of the five LCD panels to draw on (1 = leftmost ... 5 = rightmost).
# Legacy single-widget setting; superseded by WIDGETS but kept as its default.
WIDGET_PANEL = max(1, min(5, _int("WIDGET_PANEL", 1)))

# --- Widgets --------------------------------------------------------------
# Comma-separated widget:panel assignments, e.g. "claude_usage:3,energy:2".
WIDGETS = _str("WIDGETS", f"claude_usage:{WIDGET_PANEL}")
# Refresh intervals (seconds, min 15).
UPDATE_INTERVAL_SECONDS = max(15, _int("UPDATE_INTERVAL_SECONDS", 300))
ENERGY_INTERVAL_SECONDS = max(15, _int("ENERGY_INTERVAL_SECONDS", 60))
CODEX_INTERVAL_SECONDS = max(15, _int("CODEX_INTERVAL_SECONDS", UPDATE_INTERVAL_SECONDS))
# Touched after every successful update; lets a container healthcheck detect
# a wedged loop (mtime too old = unhealthy). Empty = disabled.
HEARTBEAT_FILE = _str("HEARTBEAT_FILE")
# How stale the heartbeat may get before the healthcheck fails. 0 = derive it
# from the slowest configured widget interval (see divoom_widgets.healthcheck).
HEARTBEAT_MAX_AGE_SECONDS = _int("HEARTBEAT_MAX_AGE_SECONDS", 0)

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

# --- Codex (OpenAI ChatGPT plan) via a SigNoz store ------------------------
# Codex has no usage endpoint a widget can call, but every turn it writes a
# `token_count` event carrying the plan's rolling-window usage
# (rate_limits.primary.used_percent / resets_at / window_minutes) into its
# session transcript. If those transcripts are shipped into a SigNoz log
# store, the widget reads the newest such event through SigNoz's query API.
# It reads the codex.plan.* log attributes the shipping collector lifts out of
# each token_count record, never the body (see widgets/codex_usage.py).
# SIGNOZ_URL is the query-service, e.g. http://signoz.local:8081; the API key
# comes from SigNoz → Settings → API Keys.
SIGNOZ_URL = _str("SIGNOZ_URL").rstrip("/")
SIGNOZ_API_KEY = _str("SIGNOZ_API_KEY")
SIGNOZ_API_KEY_FILE = _str("SIGNOZ_API_KEY_FILE")
# The service.name the Codex transcripts are shipped under.
CODEX_SERVICE_NAME = _str("CODEX_SERVICE_NAME", "codex-transcripts")
# How far back to look for a token_count event (days). Older = "no data".
CODEX_LOOKBACK_DAYS = max(1, _int("CODEX_LOOKBACK_DAYS", 15))

# --- OTLP metrics (optional) ----------------------------------------------
# When LO_ENDPOINT is set, every widget that exposes `metrics(data)` has its
# numbers posted as OTLP/HTTP JSON gauges after each successful fetch — so the
# plan percentages the panels show also keep a history in your metrics store
# (a 128px card forgets). Empty = off.
LO_ENDPOINT = _str("LO_ENDPOINT").rstrip("/")
LO_INGEST_TOKEN = _str("LO_INGEST_TOKEN")
LO_INGEST_TOKEN_FILE = _str("LO_INGEST_TOKEN_FILE")
LO_SERVICE_NAME = _str("LO_SERVICE_NAME", "divoom-widgets")

# --- Home Assistant (energy widget) ---------------------------------------
HA_BASE_URL = _str("HA_BASE_URL", "http://homeassistant.local:8123").rstrip("/")
# Either a long-lived access token directly, or a file whose first non-empty
# line is the token (plays well with mounted secrets).
HA_TOKEN = _str("HA_TOKEN")
HA_TOKEN_FILE = _str("HA_TOKEN_FILE")
# Entity ids are installation-specific (integrations get renamed); look yours
# up in HA under Developer Tools -> States.
HA_ENTITY_LOAD = _str("HA_ENTITY_LOAD")     # instantaneous house load
HA_ENTITY_SOLAR = _str("HA_ENTITY_SOLAR")   # instantaneous solar generation
HA_ENTITY_SOC = _str("HA_ENTITY_SOC")       # battery state of charge (%)
# Unit assumed for power entities when HA omits unit_of_measurement: kW or W.
HA_POWER_UNIT = _str("HA_POWER_UNIT", "kW")

# --- GitHub Copilot (individual Pro) --------------------------------------
GITHUB_OAUTH_TOKEN = _str("GITHUB_OAUTH_TOKEN")
GITHUB_USERNAME = _str("GITHUB_USERNAME")
# Fallback monthly premium-request allotment for Copilot Pro when the API
# does not return an explicit included amount (Pro = 300 legacy premium reqs).
COPILOT_MONTHLY_QUOTA = _int("COPILOT_MONTHLY_QUOTA", 300)


def parse_widgets(spec: str) -> list[tuple[str, int]]:
    """Parse "name:panel,name:panel" into [(name, panel), ...].

    Skips malformed entries and clamps panels to 1..5; duplicate panels are
    allowed (last writer wins on the device).
    """
    out: list[tuple[str, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, panel_s = part.partition(":")
        name = name.strip()
        if not name:
            continue
        try:
            panel = int(panel_s.strip() or "1")
        except ValueError:
            panel = 1
        out.append((name, max(1, min(5, panel))))
    return out
