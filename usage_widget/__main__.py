"""Deprecated entry point: delegates to the divoom_widgets framework.

`python -m usage_widget` keeps working for one release (it runs only the
claude_usage widget, like it always did). Switch to `python -m divoom_widgets`,
which schedules every widget configured in the WIDGETS env var.
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usage_widget")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--panel", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--interval", type=int, default=None)
    parser.add_argument("--save", default=None)
    parser.add_argument("--no-push", dest="push", action="store_false")
    args = parser.parse_args(argv)

    # Mutate env BEFORE importing divoom_widgets: its config freezes on import,
    # and loop-mode children inherit these values through the environment.
    # Load .env first so WIDGETS/WIDGET_PANEL set there are visible here.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    if args.panel is not None:
        os.environ["WIDGETS"] = f"claude_usage:{max(1, min(5, args.panel))}"
    elif "claude_usage" not in os.environ.get("WIDGETS", ""):
        # Legacy semantics: this entry point always runs the claude widget,
        # on WIDGET_PANEL, regardless of what WIDGETS configures.
        try:
            panel = int(os.environ.get("WIDGET_PANEL", "1"))
        except ValueError:
            panel = 1
        os.environ["WIDGETS"] = f"claude_usage:{max(1, min(5, panel))}"
    if args.interval is not None:
        os.environ["UPDATE_INTERVAL_SECONDS"] = str(args.interval)

    from divoom_widgets.__main__ import main as run

    print("usage_widget is deprecated; use `python -m divoom_widgets` instead",
          file=sys.stderr)
    new_argv = ["--widget", "claude_usage"]
    if args.once:
        new_argv.append("--once")
    if args.host:
        new_argv += ["--host", args.host]
    if args.save:
        new_argv += ["--save", args.save]
    if not args.push:
        new_argv.append("--no-push")
    return run(new_argv)


if __name__ == "__main__":
    sys.exit(main())
