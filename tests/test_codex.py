import datetime as dt
import math
import re

import pytest

from divoom_widgets import emit
from divoom_widgets.widgets import codex_usage as codex


@pytest.fixture(autouse=True)
def _own_cache_file(monkeypatch, tmp_path):
    # Never read or write the real wide-search cache, and never read the live
    # route: these tests are about the transcript store.
    monkeypatch.setattr(codex.config, "CODEX_CACHE_FILE", str(tmp_path / "wide.json"))
    monkeypatch.setattr(codex.config, "CODEX_AUTH_PATH", "")
    # The fixtures' reset times are real stamps from 2026; the clock is pinned
    # before all of them so the rows read as they did on the day.
    monkeypatch.setattr(codex, "_now", lambda: NOW)


NOW = dt.datetime(2026, 9, 23, 10, 0, tzinfo=dt.timezone.utc)

# A row in the shape SigNoz's v5 raw query returns for the newest
# token_count record (illustrative values: plan pro, 89% of a 10080-minute
# window, 425 token_count records with plan fields in the 8 newest buckets).
ROW = {"n": 425, "used": 89, "resets_at": 1790417902, "window_minutes": 10080,
       "plan": "pro", "rec": "2026-09-23T09:12:12.299Z"}
# What the aggregate returns for an empty window if the HAVING is ever lost:
# one row of defaults.
EMPTY_AGGREGATE = {"n": 0, "used": 0, "resets_at": 0, "window_minutes": 0,
                   "plan": "", "rec": ""}
# The row shape of the first version of the query (no `n`), still accepted.
LEGACY_ROW = {"used": 63, "resets_at": 1789292517, "window_minutes": 10080,
              "plan": "prolite", "rec": "2026-09-07T08:29:48.567Z"}
# A Plus account (illustrative values): the 5-hour window is `primary` and the
# weekly one `secondary`; 315 records with plan fields in the newest buckets,
# all of them carrying the second window.
PLUS_ROW = {"n": 315, "used": 46, "resets_at": 1793163191, "window_minutes": 300,
            "plan": "plus", "rec": "2026-10-28T00:04:12.225Z",
            "n2": 315, "used2": 15, "resets_at2": 1793652828, "window_minutes2": 10080}
# The same account read through a collector that lifts only the primary window:
# the secondary columns come back as the map defaults (0), n2 = 0.
PLUS_PRIMARY_ONLY = dict(PLUS_ROW, n2=0, used2=0, resets_at2=0, window_minutes2=0)


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


def test_normalize_reads_both_windows_of_a_plus_plan():
    out = codex.normalize(PLUS_ROW)
    assert out["ok"] is True and out["plan"] == "PLUS"
    # Shortest window first, each labelled by its length, not its position.
    assert [w["window_minutes"] for w in out["windows"]] == [300, 10080]
    assert [w["used"] for w in out["windows"]] == [46.0, 15.0]
    assert out["windows"][1]["resets_at"] == dt.datetime.fromtimestamp(1793652828, dt.timezone.utc)
    # The weekly figure is the WEEKLY one, even though the payload put it second.
    assert out["week_used"] == 15.0 and out["window_minutes"] == 10080
    assert out["week_resets_at"] == dt.datetime.fromtimestamp(1793652828, dt.timezone.utc)
    assert codex.summary(out).startswith("CDX session 46% / weekly 15% (PLUS")


def test_a_primary_only_collector_shows_the_one_window_it_has():
    # No record carried a second window: one row, named by its length — never
    # "WEEKLY 5h", which is what a position-based label produces for a Plus plan.
    out = codex.normalize(PLUS_PRIMARY_ONLY)
    assert out["ok"] is True
    assert [w["window_minutes"] for w in out["windows"]] == [300]
    assert codex.summary(out).startswith("CDX session 46% (PLUS")
    assert "weekly" not in codex.summary(out)
    pct = [m for m in codex.metrics(out) if m[0] == "ai_plan_used_percent"]
    assert [m[2]["window"] for m in pct] == ["5h"]
    # A second window without a length is the map default, not a reading.
    assert len(codex.normalize(dict(PLUS_ROW, window_minutes2=0))["windows"]) == 1
    assert len(codex.normalize(dict(PLUS_ROW, n2=0))["windows"]) == 1


def test_a_window_whose_reset_has_passed_reads_zero():
    # The newest record said 46% of the 5-hour window with a reset time 30
    # hours before `now`; a card that trusts the record still draws 46%. Now:
    # 0%, no reset pending, and the weekly window, whose reset is still ahead,
    # keeps its figure.
    later = dt.datetime(2026, 10, 29, 11, 6, tzinfo=dt.timezone.utc)
    out = codex.normalize(PLUS_ROW, now=later)
    assert [w["used"] for w in out["windows"]] == [0.0, 15.0]
    assert out["windows"][0]["resets_at"] is None and out["windows"][0]["expired"] is True
    assert out["windows"][1]["resets_at"] == dt.datetime.fromtimestamp(1793652828, dt.timezone.utc)
    assert codex.summary(out).startswith("CDX session 0% / weekly 15% (PLUS")
    pct = {m[2]["window"]: m[1] for m in codex.metrics(out) if m[0] == "ai_plan_used_percent"}
    assert pct == {"5h": 0.0, "7d": 15.0}
    # Past the weekly reset too: both read 0, the weekly figure included.
    out = codex.normalize(PLUS_ROW, now=dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc))
    assert [w["used"] for w in out["windows"]] == [0.0, 0.0] and out["week_used"] == 0.0
    # A record with no reset time cannot be expired: it keeps its figure.
    out = codex.normalize(dict(ROW, resets_at=0), now=later)
    assert out["week_used"] == 89.0 and "expired" not in out["windows"][0]
    # The default clock is the module's, so the store route expires on its own.
    assert codex.normalize(PLUS_ROW)["windows"][0]["used"] == 46.0   # NOW is pinned early
    codex._now = lambda: later
    assert codex.normalize(PLUS_ROW)["windows"][0]["used"] == 0.0


def test_fetch_uses_the_live_route_when_a_login_is_configured(monkeypatch):
    monkeypatch.setattr(codex.config, "CODEX_AUTH_PATH", "/nowhere/auth.json")
    calls = []
    monkeypatch.setattr(codex.codex_live, "get_usage", lambda: calls.append(1) or {"ok": True})
    monkeypatch.setattr(codex, "get_codex_usage", lambda: pytest.fail("store route used"))
    assert codex.fetch() == {"ok": True} and calls == [1]
    # And never falls back to the store on a live failure.
    monkeypatch.setattr(codex.codex_live, "get_usage", lambda: {"ok": False, "error": "x"})
    assert codex.fetch()["ok"] is False


def test_metrics_carry_one_gauge_per_window():
    out = codex.metrics(codex.normalize(PLUS_ROW))
    pct = {m[2]["window"]: m[1] for m in out if m[0] == "ai_plan_used_percent"}
    assert pct == {"5h": 46.0, "7d": 15.0}
    resets = {m[2]["window"]: m[1] for m in out if m[0] == "ai_plan_resets_at_seconds"}
    assert resets == {"5h": 1793163191.0, "7d": 1793652828.0}
    assert all(m[2]["plan"] == "plus" and m[2]["vendor"] == "codex" for m in out)


def test_query_reads_the_second_window_from_the_newest_record_that_has_one():
    sql = codex.query("codex-transcripts")
    assert "mapContains(attributes_number, 'codex.plan.secondary.used_percent') AS cx_has2" in sql
    for col, alias in (("cx_used2", "used2"), ("cx_resets_at2", "resets_at2"),
                       ("cx_window_min2", "window_minutes2")):
        assert re.search(rf"\bargMaxIf\({col}, cx_newest, cx_has2\)\s+AS {alias}\b", sql), alias
    assert re.search(r"\bcountIf\(cx_has2\)\s+AS n2\b", sql)
    # The newest record still decides the primary window and the plan: a host
    # that lifts only the primary window must not hide a newer turn.
    assert re.search(r"\bargMax\(cx_used, cx_newest\)\s+AS used\b", sql)


def test_render_two_windows_is_128px():
    assert codex.render(codex.normalize(PLUS_ROW)).size == (128, 128)
    assert codex.render(codex.normalize(PLUS_PRIMARY_ONLY)).size == (128, 128)


def test_normalize_accepts_the_first_query_row_shape():
    out = codex.normalize(LEGACY_ROW, now=dt.datetime(2026, 9, 7, 9, 0, tzinfo=dt.timezone.utc))
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
    assert out["ok"] is False
    assert out["error"] == f"no Codex plan record in {codex.config.CODEX_LOOKBACK_DAYS}d"


def _fake_signoz_by_buckets(monkeypatch, recent_rows, wide_rows, calls):
    """SigNoz answering the recent-bucket and the whole-lookback query apart."""
    monkeypatch.setattr(codex.config, "SIGNOZ_URL", "http://signoz.test")
    monkeypatch.setattr(codex, "_api_key", lambda: "k")

    def fake_post(url, json=None, headers=None, timeout=None):
        sql = json["compositeQuery"]["queries"][0]["spec"]["query"]
        wide = f"LIMIT {codex.wide_buckets()})" in sql
        calls.append("wide" if wide else "recent")
        return FakeResp(200, _signoz_payload(wide_rows if wide else recent_rows))

    monkeypatch.setattr(codex.requests, "post", fake_post)


def test_idle_codex_falls_back_to_the_whole_lookback(monkeypatch):
    # Codex idle for days while two open sessions kept logging
    # thread_settings_applied, so the 8 newest Codex buckets held no plan
    # record and the panel showed an error instead of the last figure.
    calls = []
    _fake_signoz_by_buckets(monkeypatch, [], [ROW], calls)
    out = codex.get_codex_usage()
    assert out["ok"] is True and out["week_used"] == 89.0
    assert calls == ["recent", "wide"]


def test_wide_search_is_cached_until_it_ages_out(monkeypatch):
    calls = []
    _fake_signoz_by_buckets(monkeypatch, [], [ROW], calls)
    now = [1_790_000_000.0]
    monkeypatch.setattr(codex.time, "time", lambda: now[0])
    assert codex.get_codex_usage()["ok"] is True
    now[0] += codex.config.CODEX_WIDE_REFRESH_SECONDS - 1
    assert codex.get_codex_usage()["week_used"] == 89.0
    assert calls == ["recent", "wide", "recent"]
    now[0] += 1
    assert codex.get_codex_usage()["ok"] is True
    assert calls == ["recent", "wide", "recent", "recent", "wide"]


def test_wide_miss_is_cached_too(monkeypatch):
    calls = []
    _fake_signoz_by_buckets(monkeypatch, [], [EMPTY_AGGREGATE], calls)
    for _ in range(3):
        assert codex.get_codex_usage()["ok"] is False
    assert calls == ["recent", "wide", "recent", "recent"]


def test_a_recent_turn_beats_the_cache(monkeypatch):
    calls = []
    _fake_signoz_by_buckets(monkeypatch, [], [ROW], calls)
    codex.get_codex_usage()
    newer = dict(ROW, used=12, rec="2026-10-02T09:00:00Z")
    _fake_signoz_by_buckets(monkeypatch, [newer], [ROW], calls)
    assert codex.get_codex_usage()["week_used"] == 12.0


def test_failed_wide_search_is_not_cached(monkeypatch):
    monkeypatch.setattr(codex.config, "SIGNOZ_URL", "http://signoz.test")
    monkeypatch.setattr(codex, "_api_key", lambda: "k")
    answers = [FakeResp(200, _signoz_payload([])), FakeResp(502, {})]
    monkeypatch.setattr(codex.requests, "post", lambda *a, **k: answers.pop(0))
    out = codex.get_codex_usage()
    assert out["ok"] is False and "502" in out["error"]
    calls = []
    _fake_signoz_by_buckets(monkeypatch, [], [ROW], calls)
    assert codex.get_codex_usage()["ok"] is True and calls == ["recent", "wide"]


def test_unreadable_cache_means_a_new_wide_search(monkeypatch):
    with open(codex.config.CODEX_CACHE_FILE, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    calls = []
    _fake_signoz_by_buckets(monkeypatch, [], [ROW], calls)
    assert codex.get_codex_usage()["ok"] is True and calls == ["recent", "wide"]


def test_wide_query_covers_every_bucket_in_the_lookback():
    sql = codex.query("codex-transcripts", codex.wide_buckets())
    assert f"LIMIT {codex.config.CODEX_LOOKBACK_DAYS * 48 + 1})" in sql
    assert sql.replace(f"LIMIT {codex.wide_buckets()})", "") == \
        codex.query("codex-transcripts").replace(f"LIMIT {codex.RECENT_BUCKETS})", "")


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
    assert out["ok"] is False and "no Codex plan record" in out["error"]


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
