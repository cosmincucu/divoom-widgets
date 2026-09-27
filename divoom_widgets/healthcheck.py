"""Container healthcheck: the loop is progressing AND the device answers.

Exit 0 only when both hold:

1. The heartbeat file was touched recently. It is written after a cycle in
   which at least one image reached the device (a data source being down does
   not stop it), so a stale heartbeat means the loop is wedged or the panels
   are not being updated.
2. The Times Gate itself responds on its local HTTP API right now. Without
   this, a device that is unplugged or has changed IP would leave the
   container looking healthy while nothing reaches the panels.

Run as: python -m divoom_widgets.healthcheck
"""
from __future__ import annotations

import os
import sys
import time

import requests

from . import config

# Default staleness bound: 3x the slowest configured widget interval, so a
# single missed cycle does not flap the status.
DEFAULT_SLACK = 3


def _max_interval() -> int:
    from . import widgets

    intervals = []
    for name, _panel in config.parse_widgets(config.WIDGETS):
        try:
            intervals.append(widgets.load(name).interval_s())
        except KeyError:
            continue
    return max(intervals) if intervals else config.UPDATE_INTERVAL_SECONDS


def heartbeat_age() -> float | None:
    """Seconds since the heartbeat file was written, or None if absent."""
    path = config.HEARTBEAT_FILE
    if not path:
        return None
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def device_responds(host: str, timeout: int = 5) -> bool:
    """True if the Times Gate answers its local HTTP API."""
    try:
        r = requests.get(f"http://{host}/get", timeout=timeout)
        return r.status_code < 500
    except requests.RequestException:
        return False


def main() -> int:
    max_age = config.HEARTBEAT_MAX_AGE_SECONDS or (_max_interval() * DEFAULT_SLACK)

    age = heartbeat_age()
    if age is None:
        print("unhealthy: no heartbeat file yet")
        return 1
    if age > max_age:
        print(f"unhealthy: heartbeat {age:.0f}s old (max {max_age}s)")
        return 1

    if not device_responds(config.DIVOOM_HOST):
        print(f"unhealthy: device {config.DIVOOM_HOST} not responding")
        return 1

    print(f"healthy: heartbeat {age:.0f}s old, device responding")
    return 0


if __name__ == "__main__":
    sys.exit(main())
