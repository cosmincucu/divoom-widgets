"""Codex (OpenAI ChatGPT plan) usage widget: the plan's rolling limits.

Two sources, one card. With CODEX_AUTH_PATH set — the widget's own Codex login —
the figures come live from the endpoint the Codex CLI's status screen reads
(codex_live.py): right now, idle or not, whichever host or app used the plan.
That is the deployment to run. Without a login, the fallback below reads what
the last Codex turn reported, which is only as current as that turn.

Codex bills by ChatGPT subscription; every turn it appends a `token_count`
event to its session transcript (`~/.codex/sessions/**/*.jsonl`) carrying the
plan's rolling-window usage:

    payload.rate_limits.primary   = {used_percent, window_minutes, resets_at (epoch s)}
    payload.rate_limits.secondary = {used_percent, window_minutes, resets_at}  (optional)
    payload.rate_limits.plan_type = "prolite" | "pro" | "plus" | ...

Which window is `primary` depends on the plan. A Pro account reports one
window, the weekly one (10080 minutes), as primary and no secondary. A Plus
account reports the 5-hour window (300) as primary and the weekly one as
secondary. The card therefore labels every window by its own length, never by
its position, and shows both when both are present — the same SESSION / WEEKLY
pair the Claude card draws.

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
    attributes_number['codex.plan.secondary.used_percent' | '...resets_at' |
                      '...window_minutes']            (when the plan has one)
    attributes_string['codex.plan.plan_type' | 'codex.timestamp']

Records shipped without those attributes are invisible to the widget. The
first version filtered on `resources_string['service.name']` and parsed JSON
out of `body`, which made ClickHouse read every log row in the 15-day window
on every poll. This one is bounded by the logs table's sort
key (`ts_bucket_start`, `resource_fingerprint`) and looks only at the newest
buckets that hold Codex records (RECENT_BUCKETS), so it reads a few granules.

"Current" is as fresh as the last Codex turn: an idle Codex reports the figure
from its last turn, so the card also says how long ago that was, and a window
whose reset time has passed since is drawn as 0% — the record's percentage
described a window that no longer exists (before this rule the card could show
a stale 5-hour percentage for a day after the window had emptied). An idle Codex with open
sessions still logs other records (thread_settings_applied), which push the
last turn out of the newest buckets within a few days. Then the widget searches
the whole lookback once and keeps that answer on disk for
CODEX_WIDE_REFRESH_SECONDS, so the idle card keeps the last figure without a
~200 MiB read on every poll.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import tempfile
import time
from typing import Any, Optional

import requests
from PIL import Image, ImageDraw

from .. import config
from ..render_kit import (
    BG, DIM, GRAY, SIZE, Fonts, header, usage_row, usage_row_compact, wrap_text,
)
from . import codex_live
from .codex_live import PLAN_LABELS as _PLAN_LABELS

CODEX_BRAND = (16, 163, 127)   # OpenAI green

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
# - The second window (`secondary`, the weekly limit on a plan whose primary is
#   the 5-hour window) comes from the newest record THAT CARRIES it (argMaxIf
#   on cx_has2): a host whose collector does not lift codex.plan.secondary.*
#   yet must not blank the weekly row every time it writes the newest turn.
#   Its three fields still come from one record. n2 = 0 means no record in
#   the searched buckets had a second window.
# - The inner columns carry a cx_ prefix so no outer alias shadows the column
#   its own aggregate reads.
_QUERY_TEMPLATE = """SELECT /* divoom-widgets codex_usage */
  count()                          AS n,
  argMax(cx_used, cx_newest)       AS used,
  argMax(cx_resets_at, cx_newest)  AS resets_at,
  argMax(cx_window_min, cx_newest) AS window_minutes,
  argMax(cx_plan, cx_newest)       AS plan,
  argMax(cx_rec, cx_newest)        AS rec,
  countIf(cx_has2)                        AS n2,
  argMaxIf(cx_used2, cx_newest, cx_has2)       AS used2,
  argMaxIf(cx_resets_at2, cx_newest, cx_has2)  AS resets_at2,
  argMaxIf(cx_window_min2, cx_newest, cx_has2) AS window_minutes2
FROM (
  SELECT
    attributes_number['codex.plan.used_percent']   AS cx_used,
    attributes_number['codex.plan.resets_at']      AS cx_resets_at,
    attributes_number['codex.plan.window_minutes'] AS cx_window_min,
    attributes_string['codex.plan.plan_type']      AS cx_plan,
    attributes_string['codex.timestamp']           AS cx_rec,
    mapContains(attributes_number, 'codex.plan.secondary.used_percent') AS cx_has2,
    attributes_number['codex.plan.secondary.used_percent']   AS cx_used2,
    attributes_number['codex.plan.secondary.resets_at']      AS cx_resets_at2,
    attributes_number['codex.plan.secondary.window_minutes'] AS cx_window_min2,
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


def wide_buckets() -> int:
    """Every 30-minute bucket in the lookback, plus the one it starts in."""
    return config.CODEX_LOOKBACK_DAYS * 48 + 1


def query(service: str, buckets: int = RECENT_BUCKETS) -> str:
    """The SQL the widget sends, with SigNoz's time templates left in."""
    if not _SERVICE_NAME.fullmatch(service or ""):
        raise ValueError(f"bad CODEX_SERVICE_NAME {service!r}")
    return (_QUERY_TEMPLATE.replace("@SERVICE@", service)
            .replace("@BUCKETS@", str(int(buckets))))


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


def _no_data(wide: bool = False) -> dict:
    # Either no Codex records in the lookback window, or none of the searched
    # buckets carries the codex.plan.* attributes. The message names what was
    # searched: the newest RECENT_BUCKETS buckets can be a few hours, not
    # CODEX_LOOKBACK_DAYS.
    if wide:
        return {"ok": False, "error":
                f"no Codex plan record in {config.CODEX_LOOKBACK_DAYS}d"}
    return {"ok": False,
            "error": f"no plan fields in newest {RECENT_BUCKETS} Codex buckets"}


def _has_row(rows: list[dict]) -> bool:
    return bool(rows) and ("n" not in rows[0] or bool(_number(rows[0].get("n"))))


def _cache_path() -> str:
    return config.CODEX_CACHE_FILE or os.path.join(
        tempfile.gettempdir(), "divoom-widgets.codex_usage.wide.json")


def _read_cache(now: float) -> Optional[dict]:
    """The last wide search, while it is younger than the refresh interval."""
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            cached = json.load(fh)
        checked = float(cached["checked_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not 0 <= now - checked < config.CODEX_WIDE_REFRESH_SECONDS:
        return None
    return cached if isinstance(cached.get("row"), (dict, type(None))) else None


def _write_cache(now: float, row: Optional[dict]) -> None:
    path = _cache_path()
    try:
        with open(f"{path}.tmp", "w", encoding="utf-8") as fh:
            json.dump({"checked_at": now, "row": row}, fh)
        os.replace(f"{path}.tmp", path)
    except OSError:
        pass   # no cache only costs another wide search next poll


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _expire(windows: list, now: dt.datetime) -> list:
    """A window whose reset time has passed since the record was written has
    emptied: its percentage is 0 now, and there is no reset pending. The
    record's figure is kept nowhere — it described a window that is gone."""
    out = []
    for w in windows:
        if w.get("resets_at") and w["resets_at"] <= now:
            w = dict(w, used=0.0, resets_at=None, expired=True)
        out.append(w)
    return out


def normalize(row: dict, now: Optional[dt.datetime] = None) -> dict:
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
    windows = [{
        "used": used,
        "resets_at": _epoch(row.get("resets_at")),
        "window_minutes": int(window_min) if window_min and window_min > 0 else 0,
    }]
    # The second window, from the newest record that carried one. n2 = 0 (or a
    # row from the first query shape, which has no n2) means there was none;
    # a window length of 0 means the record lacked it, and a used2 of 0 with
    # no window is the map's default, not a reading.
    used2 = _number(row.get("used2"))
    window_min2 = _number(row.get("window_minutes2"))
    if (_number(row.get("n2")) and used2 is not None and used2 >= 0
            and window_min2 and window_min2 > 0):
        windows.append({
            "used": used2,
            "resets_at": _epoch(row.get("resets_at2")),
            "window_minutes": int(window_min2),
        })
    # Shortest window first (SESSION above WEEKLY, as on the Claude card).
    # An unknown length sorts last so the known window keeps its place.
    windows.sort(key=lambda w: w["window_minutes"] or 10**9)
    windows = _expire(windows, now or _now())
    week = _pick(windows, 10080) or windows[-1]
    return {
        "ok": True,
        "source": "store",
        "plan": codex_live.plan_label(plan),
        "plan_raw": plan,
        "windows": windows,
        # The longest window, kept under its old names: the metrics store and
        # the summary line read the weekly figure here.
        "week_used": week["used"],
        "week_resets_at": week["resets_at"],
        "window_minutes": week["window_minutes"],
        "last_turn_at": _parse_rec(row.get("rec")),
    }


def _pick(windows: list, minutes: int) -> Optional[dict]:
    return next((w for w in windows if w["window_minutes"] == minutes), None)


def get_codex_usage() -> dict:
    if not config.SIGNOZ_URL:
        return {"ok": False, "error": "SIGNOZ_URL unset"}
    if not _api_key():
        return {"ok": False, "error": "no SigNoz API key"}
    try:
        rows = _query_rows(query(config.CODEX_SERVICE_NAME))
        if _has_row(rows):
            return normalize(rows[0])
        # The newest buckets hold no plan record. Any newer turn would have
        # landed in them, so the last wide search is still the answer until
        # it ages out.
        now = time.time()
        cached = _read_cache(now)
        if cached is None:
            rows = _query_rows(query(config.CODEX_SERVICE_NAME, wide_buckets()))
            cached = {"row": rows[0] if _has_row(rows) else None}
            _write_cache(now, cached["row"])
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:60]}
    if cached["row"] is None:
        return _no_data(wide=True)
    return normalize(cached["row"])


# --- Widget interface ------------------------------------------------------

def fetch() -> dict:
    # A login makes the card live; the store is only for a deployment without
    # one. Never both: a live failure must show as a failure, not as the
    # store's older figure wearing the same card.
    if config.CODEX_AUTH_PATH:
        return codex_live.get_usage()
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


def _window_name(minutes: int) -> str:
    """The row label: what the window is, not where the payload put it."""
    if minutes == 10080:
        return "WEEKLY"
    if minutes == 300:
        return "SESSION"
    return _window_tag(minutes).upper() if minutes else "WEEKLY"


def _windows(data: dict) -> list:
    return data.get("windows") or [{"used": data.get("week_used", 0.0),
                                    "resets_at": data.get("week_resets_at"),
                                    "window_minutes": data.get("window_minutes", 0)}]


def _is_live(data: dict) -> bool:
    return data.get("source") == "live"


def summary(data: dict) -> str:
    if data.get("ok"):
        parts = " / ".join(f"{_window_name(w['window_minutes']).lower()} {w['used']:.0f}%"
                           for w in _windows(data))
        if _is_live(data):
            return f"CDX {parts} ({data.get('plan', '')}, live)"
        return (f"CDX {parts} ({data.get('plan', '')}, "
                f"last turn {_age(data.get('last_turn_at')) or 'unknown'})")
    if data.get("relogin"):
        return f"CDX err:{data.get('error')} — {codex_live.RELOGIN_HINT}"
    return f"CDX err:{data.get('error')}"


def metrics(data: dict) -> list:
    """Gauges for the optional metrics store: the same numbers the card shows,
    one ai_plan_used_percent per window, told apart by the window tag."""
    if not data.get("ok"):
        return []
    out = []
    for w in _windows(data):
        attrs = {"vendor": "codex", "window": _window_tag(w.get("window_minutes", 0)),
                 "plan": data.get("plan_raw", "")}
        out.append(("ai_plan_used_percent", w["used"], attrs))
        if w.get("resets_at"):
            out.append(("ai_plan_resets_at_seconds", w["resets_at"].timestamp(), attrs))
        if data.get("last_turn_at"):
            out.append(("ai_plan_last_activity_seconds", data["last_turn_at"].timestamp(),
                        attrs))
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
        if data.get("relogin"):
            draw.text((5, 112), "redo the codex login", font=fonts.tiny, fill=GRAY)
        return img

    wins = _windows(data)
    live = _is_live(data)
    if len(wins) == 1:
        # One limit — the Pro shape: no 5-hour session window, the weekly cap
        # alone. It gets the full-height row, and the lower half says so,
        # rather than leaving a gap where Plus draws its second bar.
        w = wins[0]
        usage_row(draw, fonts, 28, _window_name(w["window_minutes"]),
                  _window_tag(w["window_minutes"]), w["used"], w.get("resets_at"))
        if live:
            draw.text((5, 80), "NO 5H LIMIT", font=fonts.row, fill=GRAY)
            draw.text((5, 96), f"{data.get('plan', 'this plan')}: weekly cap only",
                      font=fonts.small, fill=GRAY)
            draw.text((5, 112), _live_footer(data), font=fonts.tiny, fill=DIM)
            return img
        # Store route: one limit to draw, so the lower half says how current the
        # figure is — Codex only reports when it runs, and a day-old 60% is a
        # different message from a live one.
        draw.text((5, 80), "LAST TURN", font=fonts.row, fill=GRAY)
        draw.text((5, 96), _age(data.get("last_turn_at")) or "unknown",
                  font=fonts.small, fill=GRAY)
        draw.text((5, 112), "from codex session log", font=fonts.tiny, fill=DIM)
        return img

    # Two limits — the Plus shape (5-hour session and weekly): the compact pair,
    # then the freshness line.
    for top, w in zip((26, 60), wins[:2]):
        usage_row_compact(draw, fonts, top, _window_name(w["window_minutes"]),
                          _window_tag(w["window_minutes"]), w["used"], w.get("resets_at"))
    if live:
        draw.text((5, 98), _live_footer(data), font=fonts.small, fill=GRAY)
        draw.text((5, 114), "from chatgpt.com", font=fonts.tiny, fill=DIM)
        return img
    draw.text((5, 98), f"last turn {_age(data.get('last_turn_at')) or 'unknown'}",
              font=fonts.small, fill=GRAY)
    draw.text((5, 114), "from codex session log", font=fonts.tiny, fill=DIM)
    return img


def _live_footer(data: dict) -> str:
    """'live 11:06' — the wall-clock time of the read, in the container's TZ,
    so a card that stopped refreshing is told apart from a quiet plan."""
    when = data.get("fetched_at")
    if isinstance(when, dt.datetime):
        return f"live {when.astimezone():%H:%M}"
    return "live"
