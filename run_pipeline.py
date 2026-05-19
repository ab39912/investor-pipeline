"""
End-to-end pipeline runner.

Usage:
    python run_pipeline.py                # full run
    python run_pipeline.py --skip-ai      # skip the API-costing step
    python run_pipeline.py --quick        # tiny limits for smoke testing

This isn't strictly required — every step is a standalone CLI — but it
captures the canonical order and lets a fresh reviewer get to a final
CSV with one command.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from collectors import sec_edgar, wikidata, openvc, accelerators  # noqa: E402
from enrichment import website_enricher, ai_enricher  # noqa: E402
from validation import normalize, dedupe, validate, export  # noqa: E402

log = logging.getLogger("pipeline")


def run(args: argparse.Namespace) -> int:
    sec_limit = 50 if args.quick else (args.sec_limit or 1500)
    raw_dir = ROOT / "data/raw"
    proc_dir = ROOT / "data/processed"

    if not args.skip_collect:
        log.info("=" * 60)
        log.info("STAGE 1: COLLECTION")
        log.info("=" * 60)
        log.info("--- SEC EDGAR ---")
        sec_edgar.collect(limit=sec_limit)
        log.info("--- Wikidata ---")
        wikidata.collect()
        log.info("--- OpenVC ---")
        try:
            openvc.collect(max_pages=5 if args.quick else 50)
        except Exception as e:  # noqa: BLE001
            log.warning("OpenVC failed (likely site layout change): %s", e)
        log.info("--- Accelerators ---")
        accelerators.collect()

    log.info("=" * 60)
    log.info("STAGE 2: NORMALIZE")
    log.info("=" * 60)
    normalize.normalize_all(raw_dir, proc_dir / "normalized.jsonl")

    log.info("=" * 60)
    log.info("STAGE 3: DEDUPE")
    log.info("=" * 60)
    from collectors.utils import read_jsonl, write_jsonl
    recs = read_jsonl(proc_dir / "normalized.jsonl")
    deduped, _ = dedupe.dedupe(recs)
    write_jsonl(proc_dir / "deduped.jsonl", deduped)

    if not args.skip_web_enrich:
        log.info("=" * 60)
        log.info("STAGE 4: WEBSITE ENRICHMENT")
        log.info("=" * 60)
        website_enricher.enrich_file(
            proc_dir / "deduped.jsonl",
            proc_dir / "website_text.json",
            limit=100 if args.quick else None,
        )

    if not args.skip_ai and (proc_dir / "website_text.json").exists():
        log.info("=" * 60)
        log.info("STAGE 5: AI ENRICHMENT")
        log.info("=" * 60)
        ai_enricher.enrich(
            proc_dir / "deduped.jsonl",
            proc_dir / "website_text.json",
            proc_dir / "ai_enriched.jsonl",
            max_workers=args.workers,
            limit=50 if args.quick else None,
        )
        validation_input = proc_dir / "ai_enriched.jsonl"
    else:
        log.info("Skipping AI enrichment")
        validation_input = proc_dir / "deduped.jsonl"

    log.info("=" * 60)
    log.info("STAGE 6: VALIDATE")
    log.info("=" * 60)
    validate.validate_all(
        validation_input,
        proc_dir / "validated.jsonl",
        workers=args.workers,
        check_liveness=not args.skip_liveness,
    )

    log.info("=" * 60)
    log.info("STAGE 7: EXPORT")
    log.info("=" * 60)
    n = export.export(
        proc_dir / "validated.jsonl",
        ROOT / "output/Investor_Dataset.csv",
        ROOT / "output/Investor_Dataset.json",
        limit=args.target,
        min_conf=args.min_conf,
    )

    log.info("=" * 60)
    log.info("PIPELINE COMPLETE: %d records exported", n)
    log.info("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Investor dataset pipeline")
    parser.add_argument("--quick", action="store_true",
                        help="Smoke test with small limits (no real data quality)")
    parser.add_argument("--skip-collect", action="store_true",
                        help="Skip collection (use existing data/raw/*.jsonl)")
    parser.add_argument("--skip-web-enrich", action="store_true",
                        help="Skip website fetching")
    parser.add_argument("--skip-ai", action="store_true",
                        help="Skip Claude API enrichment (avoids API cost)")
    parser.add_argument("--skip-liveness", action="store_true",
                        help="Skip URL liveness checks (faster)")
    parser.add_argument("--sec-limit", type=int, default=1500)
    parser.add_argument("--target", type=int, default=2000,
                        help="Target final record count")
    parser.add_argument("--min-conf", type=float, default=0.3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
