"""Command-line interface.

Usage:
    python -m trumpscraper run         # full pipeline (fetch -> analyze -> report -> send)
    python -m trumpscraper fetch       # fetch + store new content only
    python -m trumpscraper analyze     # analyze stored, unprocessed content
    python -m trumpscraper report      # rebuild + print/write report (no send)
    python -m trumpscraper send        # rebuild + deliver to Telegram
    python -m trumpscraper init-db     # create the database
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import pipeline
from .config import Config
from .report import render_markdown
from .storage import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trumpscraper", description=__doc__)
    parser.add_argument("-c", "--config", help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for cmd in ("run", "fetch", "analyze", "report", "send", "init-db"):
        sub.add_parser(cmd)
    reanalyze = sub.add_parser(
        "reanalyze",
        help="re-score recently fetched items (e.g. after a prompt/logic change)",
    )
    reanalyze.add_argument(
        "--days", type=int, default=3,
        help="re-analyze items fetched within this many days (default: 3)",
    )

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    config = Config.load(args.config)

    if args.command == "run":
        pipeline.run(config)
        return 0

    if args.command == "init-db":
        Store(config.db_path).close()
        print(f"Initialized database at {config.db_path}")
        return 0

    with Store(config.db_path) as store:
        if args.command == "fetch":
            n = pipeline.fetch(config, store)
            print(f"Fetched {n} new item(s)")
        elif args.command == "analyze":
            n = pipeline.analyze(config, store)
            print(f"Analyzed {n} item(s)")
        elif args.command == "report":
            report = pipeline.build(config, store)
            pipeline.write_report_file(config, report)
            print(render_markdown(report))
        elif args.command == "send":
            report = pipeline.build(config, store)
            sent = pipeline.deliver(config, report)
            print("Sent to Telegram" if sent else "Telegram not configured / disabled")
        elif args.command == "reanalyze":
            n = pipeline.reanalyze_recent(config, store, days=args.days)
            print(f"Re-analyzed {n} item(s) from the last {args.days} day(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
