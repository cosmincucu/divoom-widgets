"""Codex (OpenAI ChatGPT plan) usage widget: the weekly plan limit.

Codex bills by ChatGPT subscription and exposes no usage endpoint a widget can
call, but every turn it appends a `token_count` event to its session
transcript (`~/.codex/sessions/**/*.jsonl`) carrying the plan's rolling-window
usage:

    payload.rate_limits.primary = {used_percent, window_minutes (10080 = 7d),
                                   resets_at (epoch s)}
    payload.rate_limits.plan_type = "prolite" | "pro" | "plus" | ...

When those transcripts are shipped into a SigNoz log store (by default under
service.name `codex-transcripts`, see CODEX_SERVICE_NAME), the newest such event is
the plan's current standing. This widget asks SigNoz's query API for it —
`POST {SIGNOZ_URL}/api/v5/query_range` with a ClickHouse SQL query — so it
needs only a SigNoz API key, not a database login.

The query never reads the transcript `body`. It needs the collector to lift
the plan fields out of each token_count record into numeric/string log
attributes before export:

    attributes_number['codex.plan.used_percent' | 'codex.plan.resets_at' |
                      'codex.plan.window_minutes']
    attributes_string['codex.plan.plan_type' | 'codex.timestamp']

Records shipped without those attributes are invisible to the widget. The
first version filtered on `resources_string['service.name']` and parsed JSON
out of `body`, which made ClickHouse read every log row in the 15-day window
on every poll. This one is bounded by the logs table's sort
key (`ts_bucket_start`, `resource_fingerprint`) and looks only at the newest
buckets that hold Codex records (RECENT_BUCKETS), so it reads a few granules.

"Current" is as fresh as the last Codex turn: an idle Codex reports the figure
from its last turn, so the card also says how long ago that was.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import time
from typing import Any, Optional

import requests
from PIL import Image, ImageDraw

from .. import config
from ..render_kit import (
    BG, DIM, GRAY, SIZE, Fonts, header, usage_row, wrap_text,
)

CODEX_BRAND = (16, 163, 127)   # OpenAI green

_PLAN_LABELS = {
    "prolite": "PRO LITE",
    "pro": "PRO",
    "plus": "PLUS",
    "team": "TEAM",
    "business": "BUSINESS",
    "enterprise": "ENTERPRISE",
    "free": "FREE",
}

# How many of the newest 30-minute log buckets that hold any Codex record the
# query looks at. The cost of the read grows with every bucket that holds a
# token_count record, so an unbounded 15-day window would keep growing until
# 15 days of records carry the attributes.
# Every Codex turn writes a token_count record, so the newest active bucket
# almost always has one; 8 buckets leave room for a bucket that only holds a
# session start. When none of them has plan fields (a host shipping Codex
# transcripts without the attribute lift has been the busiest for hours), the
# widget reports no data instead of scanning further back.
RECENT_BUCKETS = 8

# One row: the plan fields of the newest token_count record, or no row at all
# when there is none (HAVING). SigNoz substitutes the {{.start_timestamp_nano}}
# / {{.end_timestamp_nano}} templates from the request's start/end.
#
# - The WHERE starts with the sort key of signoz_logs.logs_v2
#   (ts_bucket_start, resource_fingerprint, ...). The newest active buckets
#   and the Codex fingerprints both come from the small resource table, so
#   ClickHouse reads only the Codex granules of those buckets, and the skip
#   index on attribute keys drops the ones without plan fields.
# - "Newest" is the record's own clock (codex.timestamp) first and the ingest
#   time second, so a re-shipped old record in a new bucket loses to a newer
#   turn in the same buckets.
# - Every field comes from that same newest record (argMax on one key), so a
#   reset time is never paired with another record's percentage. A missing
#   number reads as 0 and missing text as '', which normalize() treats as
#   unknown; used_percent cannot be missing because the WHERE requires it.
# - The inner columns carry a cx_ prefix so no outer alias shadows the column
#   its own aggregate reads.
_QUERY_TEMPLATE = """SELECT /* divoom-widgets codex_usage */
  count()                          AS n,
  argMax(cx_used, cx_newest)       AS used,
  argMax(cx_resets_at, cx_newest)  AS resets_at,
  argMax(cx_window_min, cx_newest) AS window_minutes,
  argMax(cx_plan, cx_newest)       AS plan,
  argMax(cx_rec, cx_newest)        AS rec
FROM (
  SELECT
    attributes_number['codex.plan.used_percent']   AS cx_used,
    attributes_number['codex.plan.resets_at']      AS cx_resets_at,
    attributes_number['codex.plan.window_minutes'] AS cx_window_min,
    attributes_string['codex.plan.plan_type']      AS cx_plan,
    attributes_string['codex.timestamp']           AS cx_rec,
    (parseDateTime64BestEffortOrZero(cx_rec, 3), timestamp) AS cx_newest
  FROM signoz_logs.distributed_logs_v2
  WHERE ts_bucket_start IN (
      SELECT DISTINCT seen_at_ts_bucket_start
      FROM signoz_logs.distributed_logs_v2_resource
      WHERE seen_at_ts_bucket_start BETWEEN intDiv({{.start_timestamp_nano}}, 1000000000) - 1800
                                        AND intDiv({{.end_timestamp_nano}}, 1000000000)
        AND labels LIKE '%"service.name":"@SERVICE@"%'
      ORDER BY seen_at_ts_bucket_start DESC
      LIMIT @BUCKETS@)
    AND resource_fingerprint IN (
      SELECT fingerprint
      FROM signoz_logs.distributed_logs_v2_resource
      WHERE seen_at_ts_bucket_start BETWEEN intDiv({{.start_timestamp_nano}}, 1000000000) - 1800
                                        AND intDiv({{.end_timestamp_nano}}, 1000000000)
        AND labels LIKE '%"service.name":"@SERVICE@"%')
    AND timestamp BETWEEN {{.start_timestamp_nano}} AND {{.end_timestamp_nano}}
    AND mapContains(attributes_number, 'codex.plan.used_percent')
)
HAVING n > 0"""

# The service name goes into the SQL as text, so only plain name characters
# are accepted.
_SERVICE_NAME = re.compile(r"[A-Za-z0-9._-]+")


def query(service: str) -> str:
    """The SQL the widget sends, with SigNoz's time templates left in."""
    if not _SERVICE_NAME.fullmatch(service or ""):
        raise ValueError(f"bad CODEX_SERVICE_NAME {service!r}")
    return (_QUERY_TEMPLATE.replace("@SERVICE@", service)
            .replace("@BUCKETS@", str(int(RECENT_BUCKETS))))


def _api_key() -> str:
    return config.secret(config.SIGNOZ_API_KEY, config.SIGNOZ_API_KEY_FILE)


def _query_rows(sql: str) -> list[dict]:
    """Run one ClickHouse SQL query through SigNoz's v5 API; return row dicts."""
    now_ms = int(time.time() * 1000)
    body = {
        "schemaVersion": "v1",
        "start": now_ms - config.CODEX_LOOKBACK_DAYS * 86_400_000,
        "end": now_ms,
        "requestType": "raw",
        "compositeQuery": {"queries": [
            {"type": "clickhouse_sql", "spec": {"name": "A", "query": sql}}]},
    }
    resp = requests.post(f"{config.SIGNOZ_URL}/api/v5/query_range", json=body,
                         headers={"Content-Type": "application/json",
                                  "SIGNOZ-API-KEY": _api_key()},
                         timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    results = (((resp.json() or {}).get("data") or {}).get("data") or {}).get("results") or []
    rows = results[0].get("rows") if results else None
    return [r.get("data") or {} for r in (rows or [])]


def _parse_rec(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    # Codex writes UTC with a Z. A stamp without a zone is read as UTC too:
    # a naive datetime would make the card's age arithmetic raise.
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def _number(value: Any) -> Optional[float]:
    """A finite number from a query cell, else None.

    SigNoz returns numbers as JSON numbers, but a 64-bit value can arrive
    quoted, so numeric text is accepted too.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _epoch(value: Any) -> Optional[dt.datetime]:
    """Epoch seconds as a UTC datetime. 0 means the field was missing."""
    num = _number(value)
    if num is None or num <= 0:
        return None
    try:
        return dt.datetime.fromtimestamp(int(num), dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _no_data() -> dict:
    # Either no Codex records in the lookback window, or none of the newest
    # RECENT_BUCKETS buckets carries the codex.plan.* attributes. The message
    # names the buckets, not the lookback: only those buckets were searched
    # for plan fields, which can be a few hours, not CODEX_LOOKBACK_DAYS.
    return {"ok": False,
            "error": f"no plan fields in newest {RECENT_BUCKETS} Codex buckets"}


def normalize(row: dict) -> dict:
    """Turn one query row into the widget's data dict.

    The aggregate row can carry defaults instead of data: n = 0 for an empty
    window, 0 or '' for a field the newest record lacks. None of those may
    reach the card or the gauges as a reading. An empty window or a missing
    percentage is an error, and a missing reset time or window length stays
    unknown instead of becoming the epoch or 0 minutes.
    """
    if "n" in row and not _number(row.get("n")):
        return _no_data()
    used = _number(row.get("used"))
    if used is None or used < 0:
        return {"ok": False, "error": "no usage in event"}
    window_min = _number(row.get("window_minutes"))
    plan = str(row.get("plan") or "")
    return {
        "ok": True,
        "plan": _PLAN_LABELS.get(plan.lower(), plan.upper() or "CODEX"),
        "plan_raw": plan,
        "week_used": used,
        "week_resets_at": _epoch(row.get("resets_at")),
        "window_minutes": int(window_min) if window_min and window_min > 0 else 0,
        "last_turn_at": _parse_rec(row.get("rec")),
    }


def get_codex_usage() -> dict:
    if not config.SIGNOZ_URL:
        return {"ok": False, "error": "SIGNOZ_URL unset"}
    if not _api_key():
        return {"ok": False, "error": "no SigNoz API key"}
    try:
        rows = _query_rows(query(config.CODEX_SERVICE_NAME))
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:60]}
    if not rows:
        return _no_data()
    return normalize(rows[0])


# --- Widget interface ------------------------------------------------------

def fetch() -> dict:
    return get_codex_usage()


def interval_s() -> int:
    return config.CODEX_INTERVAL_SECONDS


def _age(when: Optional[dt.datetime]) -> str:
    if not when:
        return ""
    secs = int((dt.datetime.now(dt.timezone.utc) - when).total_seconds())
    if secs < 60:
        return "just now"
    mins, _ = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h ago"
    if hours:
        return f"{hours}h {mins}m ago"
    return f"{mins}m ago"


def _window_tag(minutes: int) -> str:
    if minutes == 10080:
        return "7d"
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m" if minutes else "week"


def summary(data: dict) -> str:
    if data.get("ok"):
        return (f"CDX weekly {data['week_used']:.0f}% ({data.get('plan', '')}, "
                f"last turn {_age(data.get('last_turn_at')) or 'unknown'})")
    return f"CDX err:{data.get('error')}"


def metrics(data: dict) -> list:
    """Gauges for the optional metrics store: the same numbers the card shows."""
    if not data.get("ok"):
        return []
    attrs = {"vendor": "codex", "window": _window_tag(data.get("window_minutes", 0)),
             "plan": data.get("plan_raw", "")}
    out = [("ai_plan_used_percent", data["week_used"], attrs)]
    if data.get("week_resets_at"):
        out.append(("ai_plan_resets_at_seconds", data["week_resets_at"].timestamp(), attrs))
    if data.get("last_turn_at"):
        out.append(("ai_plan_last_activity_seconds", data["last_turn_at"].timestamp(), attrs))
    return out


def render(data: dict) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    draw = ImageDraw.Draw(img)
    fonts = Fonts()

    header(draw, fonts, "CODEX", CODEX_BRAND, data.get("plan", "") if data.get("ok") else "")

    if not data.get("ok"):
        draw.text((5, 52), "no data", font=fonts.title, fill=GRAY)
        for i, line in enumerate(wrap_text(draw, data.get("error", ""), fonts.tiny, SIZE - 10)):
            draw.text((5, 70 + i * 11), line, font=fonts.tiny, fill=DIM)
        return img

    usage_row(draw, fonts, 28, "WEEKLY", _window_tag(data.get("window_minutes", 0)),
              data.get("week_used", 0.0), data.get("week_resets_at"))

    # No second limit to draw (the plan reports one rolling window), so the
    # lower half says how current the figure is — Codex only reports when it
    # runs, and a day-old 60% is a different message from a live one.
    draw.text((5, 80), "LAST TURN", font=fonts.row, fill=GRAY)
    draw.text((5, 96), _age(data.get("last_turn_at")) or "unknown", font=fonts.small, fill=GRAY)
    draw.text((5, 112), "from codex session log", font=fonts.tiny, fill=DIM)
    return img
