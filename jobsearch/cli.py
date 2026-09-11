"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .config import Config, ConfigError
from .notifiers import available_notifiers
from .pipeline import OptInRequired, run
from .sources import available_sources

EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.example.yaml"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_run(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    report = run(config, dry_run=args.dry_run, explain=args.explain)
    print(report.summary())
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)
    # A source failing is worth a non-zero exit so CI surfaces it, but only
    # after everything that could be delivered has been.
    return 1 if report.errors else 0


def cmd_preview(args: argparse.Namespace) -> int:
    args.dry_run = True
    return cmd_run(args)


def cmd_sources(_: argparse.Namespace) -> int:
    print("Sources:")
    for name, cls in available_sources().items():
        flag = "  [opt-in required]" if cls.requires_opt_in else ""
        print(f"  {name:12} {cls.description}{flag}")
    print("\nNotifiers:")
    for name, cls in available_notifiers().items():
        print(f"  {name:12} {cls.description}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.config)
    if target.exists() and not args.force:
        print(f"{target} already exists; pass --force to overwrite", file=sys.stderr)
        return 1
    if not EXAMPLE_CONFIG.exists():
        print(f"missing template at {EXAMPLE_CONFIG}", file=sys.stderr)
        return 1
    target.write_text(EXAMPLE_CONFIG.read_text())
    print(f"wrote {target} — edit it, then run: jobsearch preview")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobsearch",
        description="Collect job postings and notify Discord about new matches.",
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="fetch, filter and notify")
    run_cmd.add_argument(
        "--dry-run", action="store_true", help="print matches without notifying or saving state"
    )
    run_cmd.add_argument(
        "--explain", action="store_true", help="log why each posting was filtered out"
    )
    run_cmd.set_defaults(func=cmd_run)

    preview_cmd = sub.add_parser("preview", help="alias for `run --dry-run`")
    preview_cmd.add_argument("--explain", action="store_true", help="log why postings were dropped")
    preview_cmd.set_defaults(func=cmd_preview)

    sources_cmd = sub.add_parser("sources", help="list available sources and notifiers")
    sources_cmd.set_defaults(func=cmd_sources)

    init_cmd = sub.add_parser("init", help="write a starter config.yaml")
    init_cmd.add_argument("--force", action="store_true", help="overwrite an existing config")
    init_cmd.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except (ConfigError, OptInRequired, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        # `jobsearch sources | head` closes stdout early; exit quietly rather
        # than letting the interpreter print a traceback at shutdown.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
