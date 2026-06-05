"""Command-line interface for the ESG pipeline.

    esg-pipeline demo                         # run the §10 I/O demo
    esg-pipeline process <metadata.json> [-o OUT]   # Phase 2 on one document
    esg-pipeline rss                          # poll RSS feeds once (§3.3)

Installed as the ``esg-pipeline`` console script (see pyproject.toml).
"""
from __future__ import annotations

import argparse
import json
import sys

from .common.config import settings
from .common.logging_config import configure_logging


def _cmd_demo(_args) -> int:
    from .phase2_preprocessing.orchestrator import process_raw_text

    raw = "Cng ty huong toi N3t Zer0.\n\nTong luong phat thai CO2 nam 2023 la 1500 tan."
    records = process_raw_text(
        raw, "VNM", 2023, settings.taxonomy_path,
        heading_context="Bao cao Moi truong", normalize_ocr=True,
    )
    for rec in records:
        print(json.dumps(rec, ensure_ascii=False))
    return 0


def _cmd_process(args) -> int:
    from .phase2_preprocessing.orchestrator import process_single_document

    n = process_single_document(args.metadata, args.output, settings.taxonomy_path)
    print(f"Wrote {n} ESG chunks → {args.output}")
    return 0


def _cmd_rss(_args) -> int:
    from .phase1_ingestion.crawler_tier3 import aggregate_all
    from .phase1_ingestion.deduplication import check_and_save_news, init_db

    init_db(settings.checkpoint_db)
    new = sum(
        check_and_save_news(i.as_dict(), settings.checkpoint_db) for i in aggregate_all()
    )
    print(f"Ingested {new} new news items.")
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="esg-pipeline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="Run the §10 I/O demo").set_defaults(func=_cmd_demo)

    p_proc = sub.add_parser("process", help="Run Phase 2 on one document")
    p_proc.add_argument("metadata", help="Path to the document metadata JSON")
    p_proc.add_argument("-o", "--output", default="./out", help="Output directory")
    p_proc.set_defaults(func=_cmd_process)

    sub.add_parser("rss", help="Poll RSS feeds once").set_defaults(func=_cmd_rss)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
