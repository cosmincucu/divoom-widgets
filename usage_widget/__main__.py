"""Run the usage widget: fetch usage, render a panel, push to the Times Gate.

Usage:
    python -m usage_widget                 # loop forever
    python -m usage_widget --once          # single update
    python -m usage_widget --panel 3       # draw on panel 3 (overrides env)
    python -m usage_widget --once --save out.png --no-push   # render only
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

from . import config
from .claude import get_claude_usage
from .device import TimesGate
from .render import render


def _stamp() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usage_widget")
    parser.add_argument("--once", action="store_true", help="run a single update and exit")
    parser.add_argument("--panel", type=int, default=config.WIDGET_PANEL, help="LCD panel 1-5")
    parser.add_argument("--host", default=config.DIVOOM_HOST, help="Times Gate IP")
    parser.add_argument("--interval", type=int, default=config.UPDATE_INTERVAL_SECONDS)
    parser.add_argument("--save", default=None, help="also save the rendered PNG to this path")
    parser.add_argument("--no-push", dest="push", action="store_false", help="render only; do not send")
    args = parser.parse_args(argv)

    device = TimesGate(args.host)
    panel = max(1, min(5, args.panel))

    if args.once:
        return 0 if update_once(device, panel, args.save, args.push) else 1

    print(f"usage_widget: panel {panel} on {args.host}, every {args.interval}s")
    while True:
        try:
            update_once(device, panel, args.save, args.push)
        except Exception as exc:  # never let the loop die
            print(f"[{_stamp()}] update error: {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
