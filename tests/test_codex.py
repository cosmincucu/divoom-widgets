import datetime as dt
import math
import re

from divoom_widgets import emit
from divoom_widgets.widgets import codex_usage as codex

# A row in the shape SigNoz's v5 raw query returns for the newest
# token_count record (illustrative values: plan pro, 89% of a 10080-minute
# window, 425 token_count records with plan fields in the newest buckets).
ROW = {"n": 425, "used": 89, "resets_at": 1790417902, "window_minutes": 10080,
       "plan": "pro", "rec": "2026-09-23T09:12:12.299Z"}
# What the aggregate returns for an empty window if the HAVING is ever lost:
# one row of defaults.
EMPTY_AGGREGATE = {"n": 0, "used": 0, "resets_at": 0, "window_minutes": 0,
                   "plan": "", "rec": ""}
# The row shape of the first version of the query (no `n`), still accepted.
LEGACY_ROW = {"used": 63, "resets_at": 1789292517, "window_minutes": 10080,
              "plan": "prolite", "rec": "2026-09-07T08:29:48.567Z"}


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _signoz_payload(rows):
    return {"status": "success", "data": {"type": "raw", "data": {"results": [
        {"queryName": "A", "rows": [{"data": r} for r in rows]}]}}}


def _fake_signoz(monkeypatch, rows, calls=None):
    monkeypatch.setattr(codex.config, "SIGNOZ_URL", "http://signoz.test")
    monkeypatch.setattr(codex, "_api_key", lambda: "k")

    def fake_post(url, json=None, headers=None, timeout=None):
        if calls is not None:
            calls.append((url, json, headers))
        return FakeResp(200, _signoz_payload(rows))

    monkeypatch.setattr(codex.requests, "post", fake_post)


def test_normalize_reads_the_plan_row():
    out = codex.normalize(ROW)
    assert out["ok"] is True
    assert out["week_used"] == 89.0
    assert out["plan"] == "PRO"
    assert out["plan_raw"] == "pro"
    assert out["window_minutes"] == 10080
    assert out["week_resets_at"] == dt.datetime(2026, 9, 26, 10, 18, 22, tzinfo=dt.timezone.utc)
    assert out["last_turn_at"] == dt.datetime(2026, 9, 23, 9, 12, 12, 299000,
                                              tzinfo=dt.timezone.utc)


def test_normalize_accepts_the_first_query_row_shape():
    out = codex.normalize(LEGACY_ROW)
    assert out["ok"] is True and out["plan"] == "PRO LITE"
    assert out["week_resets_at"] == dt.datetime(2026, 9, 13, 9, 41, 57, tzinfo=dt.timezone.utc)


def test_normalize_without_usage_is_an_error():
    assert codex.normalize({"plan": "pro"})["ok"] is False


def test_empty_window_never_becomes_zero_percent():
    out = codex.normalize(EMPTY_AGGREGATE)
    assert out["ok"] is False
    # Only the newest RECENT_BUCKETS buckets are searched for plan fields, so
    # the message names those and does not claim the whole lookback window.
    assert out["error"] == f"no plan fields in newest {codex.RECENT_BUCKETS} Codex buckets"
    assert f"{codex.config.CODEX_LOOKBACK_DAYS}d" not in out["error"]
    assert codex.metrics(out) == []


def test_malformed_usage_is_an_error_not_a_reading():
    for bad in ("abc", "", float("nan"), float("inf"), -1, True, None, [89]):
        out = codex.normalize(dict(ROW, used=bad))
        assert out["ok"] is False, bad
        assert codex.metrics(out) == [], bad


def test_quoted_numbers_are_read():
    out = codex.normalize(dict(ROW, n="425", used="89.5", resets_at="1790417902",
                               window_minutes="10080"))
    assert out["ok"] is True and out["week_used"] == 89.5
    assert out["week_resets_at"].year == 2026 and out["window_minutes"] == 10080


def test_missing_reset_and_window_stay_unknown():
    # The query reads a missing map key as 0; that must not become the epoch
    # (a reset in 1970) or a zero-minute window.
    out = codex.normalize(dict(ROW, resets_at=0, window_minutes=0, plan="", rec=""))
    assert out["ok"] is True and out["week_used"] == 89.0
    assert out["week_resets_at"] is None
    assert out["window_minutes"] == 0
    assert out["plan"] == "CODEX" and out["last_turn_at"] is None
    names = {m[0] for m in codex.metrics(out)}
    assert names == {"ai_plan_used_percent"}
    assert codex.normalize(dict(ROW, resets_at=1e300))["week_resets_at"] is None


def test_record_time_without_zone_is_utc():
    out = codex.normalize(dict(ROW, rec="2026-09-23T09:12:12"))
    assert out["last_turn_at"] == dt.datetime(2026, 9, 23, 9, 12, 12, tzinfo=dt.timezone.utc)
    assert "ago" in codex.summary(out)


def test_query_reads_parsed_attributes_not_the_body():
    sql = codex.query("codex-transcripts")
    # Scanning the transcript body made every poll read the whole window; the query must not
    # name it anywhere.
    assert not re.search(r"\bbody\b", sql)
    assert "attributes_number['codex.plan.used_percent']" in sql
    assert "mapContains(attributes_number, 'codex.plan.used_percent')" in sql
    assert "timestamp BETWEEN {{.start_timestamp_nano}} AND {{.end_timestamp_nano}}" in sql
    # An empty window returns no row rather than a row of zeros.
    assert sql.rstrip().endswith("HAVING n > 0")
    # No placeholder is left for SigNoz to trip over.
    assert "@" not in sql


def test_query_takes_every_field_from_the_newest_record_by_record_time():
    sql = codex.query("codex-transcripts")
    # "Newest" is the record's own clock first and the ingest time second.
    # Ordering by ingest time alone would let a re-shipped batch, which lands
    # a day of old records under one ingest stamp, beat a newer turn.
    assert re.search(r"attributes_string\['codex\.timestamp'\]\s+AS cx_rec\b", sql)
    assert "(parseDateTime64BestEffortOrZero(cx_rec, 3), timestamp) AS cx_newest" in sql
    # Every field comes from that one record, so a reset time is never paired
    # with another record's percentage.
    for col, alias in (("cx_used", "used"), ("cx_resets_at", "resets_at"),
                       ("cx_window_min", "window_minutes"), ("cx_plan", "plan"),
                       ("cx_rec", "rec")):
        assert re.search(rf"\bargMax\({col}, cx_newest\)\s+AS {alias}\b", sql), alias
    assert sql.count("argMax(") == 5
    # No other aggregate picks a value from an arbitrary or different record.
    assert not re.search(r"\b(any|anyLast|anyHeavy|min|max|argMin)\(", sql)


def test_query_is_bounded_by_the_sort_key_and_recent_buckets():
    sql = codex.query("codex-transcripts")
    where = sql.split("FROM signoz_logs.distributed_logs_v2\n", 1)[1]
    # Sort-key order: the bucket first, then the resource fingerprint.
    assert where.lstrip().startswith("WHERE ts_bucket_start IN (")
    assert where.index("ts_bucket_start IN (") < where.index("resource_fingerprint IN (")
    # The buckets are the newest ones holding Codex records, at most
    # RECENT_BUCKETS of them, taken from the resource table.
    assert ("ORDER BY seen_at_ts_bucket_start DESC\n"
            f"      LIMIT {codex.RECENT_BUCKETS})") in sql
    assert sql.count("labels LIKE '%\"service.name\":\"codex-transcripts\"%'") == 2
    assert sql.count("BETWEEN intDiv({{.start_timestamp_nano}}, 1000000000) - 1800") == 2
    assert 1 <= codex.RECENT_BUCKETS <= 48


def test_query_rejects_a_service_name_that_is_not_a_name():
    for bad in ("", "codex'; DROP", 'a"b', "a%b", "a\\b", None):
        try:
            codex.query(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_query_never_reads_the_gauges_it_emits():
    # The widget posts ai_plan_* gauges. Reading those back would let panel
    # polls keep a dead source looking fresh, so the source must stay the logs.
    sql = codex.query("codex-transcripts")
    assert "signoz_logs.distributed_logs_v2" in sql
    assert "signoz_metrics" not in sql and "ai_plan_" not in sql


def test_get_codex_usage_success(monkeypatch):
    calls = []
    _fake_signoz(monkeypatch, [ROW], calls)
    out = codex.get_codex_usage()
    assert out["ok"] is True and out["week_used"] == 89.0
    url, body, headers = calls[0]
    assert url == "http://signoz.test/api/v5/query_range"
    assert headers["SIGNOZ-API-KEY"] == "k"
    assert body["requestType"] == "raw"
    assert body["end"] - body["start"] == codex.config.CODEX_LOOKBACK_DAYS * 86_400_000
    sql = body["compositeQuery"]["queries"][0]["spec"]["query"]
    assert "codex-transcripts" in sql and "{{.start_timestamp_nano}}" in sql
    assert not re.search(r"\bbody\b", sql)


def test_get_codex_usage_bad_service_name_sends_nothing(monkeypatch):
    calls = []
    _fake_signoz(monkeypatch, [ROW], calls)
    monkeypatch.setattr(codex.config, "CODEX_SERVICE_NAME", "codex'x")
    out = codex.get_codex_usage()
    assert out["ok"] is False and "CODEX_SERVICE_NAME" in out["error"]
    assert calls == []


def test_get_codex_usage_empty_aggregate_is_no_data(monkeypatch):
    _fake_signoz(monkeypatch, [EMPTY_AGGREGATE])
    out = codex.get_codex_usage()
    assert out["ok"] is False and "no plan fields" in out["error"]


def test_fetch_to_gauges_keeps_source_time_apart_from_emission_time(monkeypatch):
    # Through the real fetch path: the last-activity gauge carries the Codex
    # record's own time, while the point is stamped with the emission time.
    _fake_signoz(monkeypatch, [ROW])
    data = codex.fetch()
    emitted_ns = 1_790_200_000 * 10**9
    body = emit.payload(codex.metrics(data), service="divoom-widgets", now_ns=emitted_ns)
    by_name = {m["name"]: m["gauge"]["dataPoints"][0]
               for m in body["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]}
    turn = dt.datetime(2026, 9, 23, 9, 12, 12, 299000, tzinfo=dt.timezone.utc).timestamp()
    assert math.isclose(by_name["ai_plan_last_activity_seconds"]["asDouble"], turn)
    assert by_name["ai_plan_last_activity_seconds"]["timeUnixNano"] == str(emitted_ns)
    assert by_name["ai_plan_used_percent"]["asDouble"] == 89.0
    assert by_name["ai_plan_resets_at_seconds"]["asDouble"] == 1790417902.0
    attrs = {a["key"]: a["value"]["stringValue"]
             for a in by_name["ai_plan_used_percent"]["attributes"]}
    assert attrs == {"vendor": "codex", "window": "7d", "plan": "pro"}


def test_get_codex_usage_no_rows(monkeypatch):
    monkeypatch.setattr(codex.config, "SIGNOZ_URL", "http://signoz.test")
    monkeypatch.setattr(codex, "_api_key", lambda: "k")
    monkeypatch.setattr(codex.requests, "post",
                        lambda *a, **k: FakeResp(200, _signoz_payload([])))
    out = codex.get_codex_usage()
    assert out["ok"] is False and "no plan fields" in out["error"]


def test_get_codex_usage_http_error(monkeypatch):
    monkeypatch.setattr(codex.config, "SIGNOZ_URL", "http://signoz.test")
    monkeypatch.setattr(codex, "_api_key", lambda: "k")
    monkeypatch.setattr(codex.requests, "post", lambda *a, **k: FakeResp(401, {}))
    out = codex.get_codex_usage()
    assert out["ok"] is False and "401" in out["error"]


def test_get_codex_usage_unconfigured(monkeypatch):
    monkeypatch.setattr(codex.config, "SIGNOZ_URL", "")
    assert codex.get_codex_usage()["ok"] is False


def test_metrics_carry_vendor_and_window():
    out = codex.metrics(codex.normalize(ROW))
    names = {m[0] for m in out}
    assert names == {"ai_plan_used_percent", "ai_plan_resets_at_seconds",
                     "ai_plan_last_activity_seconds"}
    pct = next(m for m in out if m[0] == "ai_plan_used_percent")
    assert pct[1] == 89.0
    assert pct[2] == {"vendor": "codex", "window": "7d", "plan": "pro"}
    assert codex.metrics({"ok": False}) == []


def test_render_ok_and_error_are_128px():
    img = codex.render(codex.normalize(ROW))
    assert img.size == (128, 128)
    unknown = codex.render(codex.normalize(dict(ROW, resets_at=0, window_minutes=0,
                                                plan="", rec="")))
    assert unknown.size == (128, 128)
    err = codex.render({"ok": False, "error": "no SigNoz API key"})
    assert err.size == (128, 128)


def test_summary_names_the_age():
    data = codex.normalize(dict(ROW, rec=(dt.datetime.now(dt.timezone.utc)
                                          - dt.timedelta(minutes=5)).isoformat()))
    assert "5m ago" in codex.summary(data)
    assert "CDX err" in codex.summary({"ok": False, "error": "x"})
