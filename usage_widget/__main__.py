"""Run the usage widget: fetch usage, render a panel, push to the Times Gate.

Usage:
    python -m usage_widget                 # supervised loop forever
    python -m usage_widget --once          # single update
    python -m usage_widget --panel 3       # draw on panel 3 (overrides env)
    python -m usage_widget --once --save out.png --no-push   # render only

Loop mode runs each update as a short-lived **subprocess with a hard
kill-timeout** instead of calling the work inline. A single update can hang
indefinitely (e.g. DNS resolution after the machine wakes from sleep is not
covered by requests' timeout); supervising it as a subprocess means a hung
cycle is killed and the next one still runs. Progress is written to a logfile
(DIVOOM_WIDGET_LOG) so it can be inspected even under pythonw.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import socket
import subprocess
import sys
import time

from . import config
from .claude import get_claude_usage
from .device import TimesGate
from .render import render

LOG_FILE = os.environ.get(
    "DIVOOM_WIDGET_LOG", os.path.join(os.path.expanduser("~"), ".divoom-usage-widget.log")
)


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    line = f"[{_stamp()}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _summ(claude: dict) -> str:
    if claude.get("ok"):
        return f"CLD session {claude['session_used']:.0f}% / weekly {claude['week_used']:.0f}%"
    return f"CLD err:{claude.get('error')}"


def update_once(device: TimesGate, panel: int, save: str | None, push: bool) -> bool:
    claude = get_claude_usage()
    image = render(claude)
    if save:
        image.save(save)
    detail = "(not pushed)"
    ok = True
    if push:
        ok, detail = device.push_image(image, panel)
    print(f"[{_stamp()}] {_summ(claude)} -> panel {panel}: {'OK' if ok else 'FAIL'} {detail}")
    return ok


def _supervise(panel: int, host: str, interval: int) -> int:
    """Loop: run `--once` as a subprocess each cycle, bounded by a kill-timeout."""
    child_timeout = max(60, min(120, interval - 10))
    cmd = [sys.executable, "-m", "usage_widget", "--once", "--panel", str(panel), "--host", host]
    _log(f"supervisor start: panel {panel} on {host}, every {interval}s "
         f"(child timeout {child_timeout}s); log {LOG_FILE}")
    while True:
        started = time.time()
        try:
            r = subprocess.run(cmd, timeout=child_timeout, capture_output=True, text=True)
            out = (r.stdout or "").strip() or (r.stderr or "").strip() or f"exit {r.returncode}"
            _log(f"cycle {time.time() - started:.0f}s: {out}")
        except subprocess.TimeoutExpired:
            _log(f"cycle TIMED OUT after {child_timeout}s — killed (likely a hung network call)")
        except Exception as exc:  # supervisor must never die
            _log(f"cycle error: {exc}")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usage_widget")
    parser.add_argument("--once", action="store_true", help="run a single update and exit")
    parser.add_argument("--panel", type=int, default=config.WIDGET_PANEL, help="LCD panel 1-5")
    parser.add_argument("--host", default=config.DIVOOM_HOST, help="Times Gate IP")
    parser.add_argument("--interval", type=int, default=config.UPDATE_INTERVAL_SECONDS)
    parser.add_argument("--save", default=None, help="also save the rendered PNG to this path")
    parser.add_argument("--no-push", dest="push", action="store_false", help="render only; do not send")
    args = parser.parse_args(argv)

    panel = max(1, min(5, args.panel))

    if args.once:
        # Bound blocking socket ops so a single update can't hang forever.
        socket.setdefaulttimeout(30)
        device = TimesGate(args.host)
        return 0 if update_once(device, panel, args.save, args.push) else 1

    return _supervise(panel, args.host, args.interval)


if __name__ == "__main__":
    sys.exit(main())
