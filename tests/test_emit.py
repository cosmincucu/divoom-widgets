from divoom_widgets import emit


class FakeResp:
    def __init__(self, status):
        self.status_code = status


def test_payload_groups_points_by_metric_name():
    body = emit.payload([
        ("ai_plan_used_percent", 25.0, {"vendor": "claude", "window": "seven_day"}),
        ("ai_plan_used_percent", 63.0, {"vendor": "codex", "window": "7d"}),
        ("ai_plan_resets_at_seconds", 1789292517, {"vendor": "codex"}),
        ("skipped", None, {}),
    ], service="divoom-widgets", now_ns=1_000)
    rm = body["resourceMetrics"][0]
    assert rm["resource"]["attributes"][0]["value"]["stringValue"] == "divoom-widgets"
    metrics = {m["name"]: m for m in rm["scopeMetrics"][0]["metrics"]}
    assert set(metrics) == {"ai_plan_used_percent", "ai_plan_resets_at_seconds"}
    dps = metrics["ai_plan_used_percent"]["gauge"]["dataPoints"]
    assert [d["asDouble"] for d in dps] == [25.0, 63.0]
    assert dps[0]["timeUnixNano"] == "1000"
    assert {a["key"]: a["value"]["stringValue"] for a in dps[0]["attributes"]} == {
        "vendor": "claude", "window": "seven_day"}


def test_gauges_is_a_noop_without_endpoint(monkeypatch):
    monkeypatch.setattr(emit.config, "LO_ENDPOINT", "")
    assert emit.gauges([("x", 1.0, {})]) == (False, "metrics off")


def test_gauges_posts_with_bearer_token(monkeypatch):
    monkeypatch.setattr(emit.config, "LO_ENDPOINT", "http://lo.test:4318")
    monkeypatch.setattr(emit.config, "LO_INGEST_TOKEN", "tok")
    monkeypatch.setattr(emit.config, "LO_INGEST_TOKEN_FILE", "")
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return FakeResp(200)

    monkeypatch.setattr(emit.requests, "post", fake_post)
    ok, detail = emit.gauges([("x", 1.0, {"a": "b"})], service="svc")
    assert ok and detail == "metrics http 200"
    assert seen["url"] == "http://lo.test:4318/v1/metrics"
    assert seen["headers"]["Authorization"] == "Bearer tok"


def test_gauges_reports_failures_without_raising(monkeypatch):
    monkeypatch.setattr(emit.config, "LO_ENDPOINT", "http://lo.test:4318")
    monkeypatch.setattr(emit.config, "LO_INGEST_TOKEN", "")
    monkeypatch.setattr(emit.config, "LO_INGEST_TOKEN_FILE", "")

    def boom(*a, **k):
        raise emit.requests.ConnectionError("down")

    monkeypatch.setattr(emit.requests, "post", boom)
    ok, detail = emit.gauges([("x", 1.0, {})])
    assert ok is False and detail.startswith("metrics:")
    monkeypatch.setattr(emit.requests, "post", lambda *a, **k: FakeResp(503))
    assert emit.gauges([("x", 1.0, {})]) == (False, "metrics http 503")


def test_gauges_with_no_points_does_not_post(monkeypatch):
    monkeypatch.setattr(emit.config, "LO_ENDPOINT", "http://lo.test:4318")
    monkeypatch.setattr(emit.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert emit.gauges([]) == (True, "no points")
