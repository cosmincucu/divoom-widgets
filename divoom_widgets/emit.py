"""Optional OTLP/HTTP JSON metrics emission.

A widget that exposes `metrics(data) -> list[(name, value, attrs)]` gets those
gauges posted to `LO_ENDPOINT/v1/metrics` after every successful fetch. Plain
`requests`, no OpenTelemetry SDK: the payload is a few lines of JSON and the
SDK would triple the image for it.

Off unless LO_ENDPOINT is set. Never raises — a metrics store being down must
not stop the panel from updating.
"""
from __future__ import annotations

import time
from typing import Iterable, Tuple

import requests

from . import config

Point = Tuple[str, float, dict]


def enabled() -> bool:
    return bool(config.LO_ENDPOINT)


def _token() -> str:
    return config.secret(config.LO_INGEST_TOKEN, config.LO_INGEST_TOKEN_FILE)


def payload(points: Iterable[Point], service: str, now_ns: int | None = None) -> dict:
    """OTLP/JSON ResourceMetrics: one gauge per distinct metric name, one data
    point per (value, attrs). Pure, so it is testable without a network."""
    now_ns = now_ns or int(time.time() * 1e9)
    by_name: dict[str, list] = {}
    for name, value, attrs in points:
        if value is None:
            continue
        by_name.setdefault(name, []).append({
            "asDouble": float(value),
            "timeUnixNano": str(now_ns),
            "attributes": [{"key": k, "value": {"stringValue": str(v)}}
                           for k, v in sorted((attrs or {}).items())],
        })
    metrics = [{"name": name, "gauge": {"dataPoints": dps}} for name, dps in by_name.items()]
    return {"resourceMetrics": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": service}}]},
        "scopeMetrics": [{"scope": {"name": "divoom-widgets"}, "metrics": metrics}],
    }]}


def gauges(points: Iterable[Point], service: str | None = None) -> tuple[bool, str]:
    """Post the points. Returns (ok, detail); (False, 'metrics off') when unset."""
    if not enabled():
        return False, "metrics off"
    body = payload(points, service or config.LO_SERVICE_NAME)
    if not body["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]:
        return True, "no points"
    headers = {"Content-Type": "application/json"}
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        resp = requests.post(f"{config.LO_ENDPOINT}/v1/metrics", json=body,
                             headers=headers, timeout=10)
    except requests.RequestException as exc:
        return False, f"metrics: {str(exc)[:80]}"
    if 200 <= resp.status_code < 300:
        return True, f"metrics http {resp.status_code}"
    return False, f"metrics http {resp.status_code}"
