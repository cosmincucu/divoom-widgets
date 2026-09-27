"""Run the widget framework: fetch, render, and push each widget to its panel.

Usage:
    python -m divoom_widgets                       # supervised loop forever
    python -m divoom_widgets --once                # single update of all widgets
    python -m divoom_widgets --once --widget energy --no-push --save out.png

Widgets and panel assignments come from the WIDGETS env var
(e.g. "claude_usage:3,energy:2"); each widget refreshes on its own interval.

Loop mode runs one supervision thread per widget, and each update as a
short-lived **subprocess with a hard kill-timeout** instead of calling the
work inline. A single update can hang indefinitely (e.g. DNS resolution after
the machine wakes from sleep is not covered by requests' timeout); supervising
it as a subprocess means a hung cycle is killed, and the per-widget threads
mean it never delays the other widgets' cadences. Progress is written to a
logfile (DIVOOM_WIDGET_LOG, size-capped with one .1 rotation) so it can be
inspected even under pythonw.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

from . import config, emit, widgets
from .device import TimesGate

LOG_FILE = os.environ.get(
    "DIVOOM_WIDGET_LOG", os.path.join(os.path.expanduser("~"), ".divoom-usage-widget.log")
)
# One-deep rotation (.1 suffix) once the logfile passes this size.
LOG_MAX_BYTES = 1_000_000
_log_lock = threading.Lock()


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    line = f"[{_stamp()}] {msg}"
    print(line, flush=True)
    with _log_lock:
        try:
            if os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
                os.replace(LOG_FILE, LOG_FILE + ".1")
        except OSError:
            pass
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _heartbeat() -> None:
    if not config.HEARTBEAT_FILE:
        return
    try:
        with open(config.HEARTBEAT_FILE, "w", encoding="utf-8") as fh:
            fh.write(_stamp())
    except OSError:
        pass


def _last_success_path(name: str) -> str:
    """Per-widget marker file. Each update runs as its own subprocess, so
    'how long has this widget been failing' has to live on disk."""
    base = config.HEARTBEAT_FILE or os.path.join(
        tempfile.gettempdir(), "divoom-widgets")
    return f"{base}.{name}.ok"


def _mark_success(name: str) -> None:
    try:
        with open(_last_success_path(name), "w", encoding="utf-8") as fh:
            fh.write(_stamp())
    except OSError:
        pass


def _failing_for(name: str) -> Optional[float]:
    """Seconds since this widget last succeeded, or None if never/unknown."""
    try:
        return time.time() - os.path.getmtime(_last_success_path(name))
    except OSError:
        return None


def _save_path(base: str, name: str, many: bool) -> str:
    if not many:
        return base
    root, ext = os.path.splitext(base)
    return f"{root}-{name}{ext or '.png'}"


def _emit_metrics(name: str, mod, data: dict) -> str:
    """Post the widget's gauges to the metrics store, if configured. Returns a
    short note for the log line ('' when metrics are off or fine)."""
    if not emit.enabled() or not hasattr(mod, "metrics"):
        return ""
    try:
        ok, detail = emit.gauges(mod.metrics(data))
    except Exception as exc:  # a metrics bug must never cost the panel
        return f" [{name} metrics error: {str(exc)[:60]}]"
    return "" if ok else f" [{detail}]"


def update_once(device: TimesGate, jobs: list[tuple[str, int]],
                save: str | None, push: bool) -> bool:
    """Run one update for each (widget, panel) job. True if all succeeded."""
    all_ok = True
    # The heartbeat answers "is the loop running and does the device accept
    # draws" — that is what the healthcheck exists to detect. A data source
    # being down (a dead token, HA offline) shows on its own panel and in the
    # log; it must not mark the whole container unhealthy indefinitely.
    pushed_ok = False
    for name, panel in jobs:
        mod = widgets.load(name)
        data = mod.fetch()
        if not data.get("ok"):
            # A blip should not replace a good card, but a widget that has been
            # down for several cycles must say so — otherwise the panel shows
            # stale numbers indefinitely and the failure goes unnoticed.
            failing = _failing_for(name)
            persistent = failing is None or failing > 3 * mod.interval_s()
            if not persistent:
                print(f"[{_stamp()}] {mod.summary(data)} -> panel {panel}: SKIP (fetch failed)")
                all_ok = False
                continue
            since = "never succeeded" if failing is None else f"failing for {failing:.0f}s"
            print(f"[{_stamp()}] {mod.summary(data)} -> panel {panel}: error card ({since})")
            all_ok = False
        # Good data reaches the metrics store whether or not the panel takes
        # the draw: the store is the durable record, the panel is a display.
        note = _emit_metrics(name, mod, data) if data.get("ok") else ""
        image = mod.render(data)
        if save:
            image.save(_save_path(save, name, many=len(jobs) > 1))
        ok, detail = (True, "(not pushed)") if not push else device.push_image(image, panel)
        pushed_ok = pushed_ok or ok
        if data.get("ok"):
            print(f"[{_stamp()}] {mod.summary(data)} -> panel {panel}: "
                  f"{'OK' if ok else 'FAIL'} {detail}{note}")
            if ok:
                _mark_success(name)
            all_ok = all_ok and ok
    if pushed_ok:
        _heartbeat()
    return all_ok


def _child_cmd(name: str, host: str, push: bool, save: str | None) -> list[str]:
    cmd = [sys.executable, "-m", "divoom_widgets",
           "--once", "--widget", name, "--host", host]
    if not push:
        cmd.append("--no-push")
    if save:
        cmd += ["--save", save]
    return cmd


# A widget whose SOURCE is broken must not keep polling at full rate: two days
# of retrying an expired Claude token at 150s got the caller rate-limited.
BACKOFF_CAP_SECONDS = 1800

# Failing to reach the PANEL is a different failure and needs a different cap.
# The Times Gate is a local device on the LAN, so there is no third party to
# protect from a retry -- and a long cap only means the panels stay stale after
# the device comes back. Measured 2026-08-26: the panel wedges and recovers on
# its own (15s, 55s, 2m44s, then 19.5h), and at the 1800s cap the display stayed
# blank for up to half an hour after each recovery, for no benefit.
TRANSPORT_BACKOFF_CAP_SECONDS = 120

# What a child prints when the push failed at the transport rather than the
# source; device.push_image raises this class from requests.RequestException.
TRANSPORT_MARKER = "FAIL transport:"


def _backoff(interval: int, failures: int, transport: bool = False) -> float:
    """Delay before the next attempt: normal cadence until a widget starts
    failing, then doubling up to the cap for that failure class."""
    if failures <= 0:
        return float(interval)
    cap = TRANSPORT_BACKOFF_CAP_SECONDS if transport else BACKOFF_CAP_SECONDS
    return float(min(interval * (2 ** min(failures, 10)), cap))


def _widget_loop(name: str, host: str, interval: int, push: bool, save: str | None) -> None:
    """One widget's forever-loop: run `--once` children on this widget's cadence."""
    child_timeout = max(60, min(120, interval - 10))
    cmd = _child_cmd(name, host, push, save)
    failures = 0
    transport = False
    while True:
        started = time.time()
        try:
            r = subprocess.run(cmd, timeout=child_timeout, capture_output=True, text=True)
            out = (r.stdout or "").strip() or (r.stderr or "").strip() or f"exit {r.returncode}"
            failures = 0 if r.returncode == 0 else failures + 1
            transport = bool(failures) and TRANSPORT_MARKER in out
            _log(f"{name} cycle {time.time() - started:.0f}s: {out}")
        except subprocess.TimeoutExpired:
            failures += 1
            _log(f"{name} cycle TIMED OUT after {child_timeout}s — killed "
                 "(likely a hung network call)")
        except Exception as exc:  # the loop must never die
            failures += 1
            _log(f"{name} cycle error: {exc}")
        delay = _backoff(interval, failures, transport)
        if failures:
            _log(f"{name} failing ({failures}x) — next attempt in {delay:.0f}s")
        time.sleep(max(1.0, delay - (time.time() - started)))


def _supervise(jobs: list[tuple[str, int]], host: str,
               push: bool, save: str | None) -> int:
    """Run one supervision thread per widget so a hung widget never delays
    the others (each update is still a killable subprocess)."""
    intervals = {name: widgets.load(name).interval_s() for name, _ in jobs}
    _log("supervisor start: " +
         ", ".join(f"{n}:p{p}@{intervals[n]}s" for n, p in jobs) +
         f" on {host}; log {LOG_FILE}" +
         (f"; metrics -> {config.LO_ENDPOINT}" if emit.enabled() else "; metrics off"))
    threads = [
        threading.Thread(target=_widget_loop, name=f"widget-{name}",
                         args=(name, host, intervals[name], push, save), daemon=True)
        for name, _ in jobs
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="divoom_widgets")
    parser.add_argument("--once", action="store_true", help="run a single update and exit")
    parser.add_argument("--widget", default=None,
                        help="run only this widget (must be in WIDGETS)")
    parser.add_argument("--host", default=config.DIVOOM_HOST, help="Times Gate IP")
    parser.add_argument("--save", default=None,
                        help="also save rendered PNG(s); multi-widget runs add a -<name> suffix")
    parser.add_argument("--no-push", dest="push", action="store_false",
                        help="render only; do not send")
    args = parser.parse_args(argv)

    jobs = config.parse_widgets(config.WIDGETS)
    if not jobs:
        print(f"[{_stamp()}] no widgets configured (WIDGETS={config.WIDGETS!r})")
        return 2
    if args.widget:
        jobs = [(n, p) for n, p in jobs if n == args.widget]
        if not jobs:
            print(f"[{_stamp()}] widget {args.widget!r} is not in WIDGETS "
                  f"({config.WIDGETS!r}); add it there (name:panel) to run it")
            return 2
    for name, _ in jobs:
        widgets.load(name)  # fail fast on unknown widget names

    if args.once:
        # Bound blocking socket ops so a single update can't hang forever.
        socket.setdefaulttimeout(30)
        device = TimesGate(args.host)
        return 0 if update_once(device, jobs, args.save, args.push) else 1

    return _supervise(jobs, args.host, args.push, args.save)


if __name__ == "__main__":
    sys.exit(main())
