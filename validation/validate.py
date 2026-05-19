"""
Validator — quality checks + confidence scoring.

Runs after dedup. Adds/updates:
  - URL liveness (HEAD request, cached per domain)
  - Email format validation
  - Confidence score adjustment based on completeness

We DON'T drop records here. Validation produces a quality score and writes
notes; the dataset-export step decides what to keep based on flags.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.schema import MANDATORY_FIELDS  # noqa: E402
from collectors.utils import Throttle, make_session, read_jsonl, write_jsonl  # noqa: E402

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[\w\.\+\-]+@[\w\-]+\.[\w\.\-]+$")


def url_live(session: requests.Session, url: str, cache: dict[str, bool]) -> bool:
    """HEAD check on host root; results cached per host."""
    host = urlparse(url).netloc.lower()
    if not host:
        return False
    if host in cache:
        return cache[host]
    try:
        r = session.head(f"https://{host}", timeout=8, allow_redirects=True)
        ok = r.status_code < 500
    except Exception:  # noqa: BLE001
        ok = False
    cache[host] = ok
    return ok


def completeness_score(rec: dict) -> float:
    """Fraction of mandatory + nice-to-have fields populated."""
    from collectors.schema import OPTIONAL_FIELDS
    populated_mandatory = sum(1 for k in MANDATORY_FIELDS if rec.get(k))
    populated_optional = sum(1 for k in OPTIONAL_FIELDS if rec.get(k))
    score = (populated_mandatory / len(MANDATORY_FIELDS)) * 0.7 + \
            (populated_optional / len(OPTIONAL_FIELDS)) * 0.3
    return round(score, 3)


def validate_one(rec: dict, session: requests.Session, url_cache: dict[str, bool]) -> dict:
    notes_parts = [rec.get("notes")] if rec.get("notes") else []
    flags = []

    # Mandatory field check
    missing = [k for k in MANDATORY_FIELDS if not rec.get(k)]
    if missing:
        flags.append(f"missing_mandatory:{','.join(missing)}")

    # Email format
    email = rec.get("contact_email")
    if email and not EMAIL_RE.match(email):
        flags.append("bad_email")
        rec["contact_email"] = None

    # URL liveness
    if rec.get("website"):
        if not url_live(session, rec["website"], url_cache):
            flags.append("dead_website")

    completeness = completeness_score(rec)
    # Blend completeness with existing confidence
    existing = rec.get("confidence_score")
    if existing is None:
        existing = 0.7
    rec["confidence_score"] = round(0.6 * existing + 0.4 * completeness, 2)

    if flags:
        notes_parts.append("FLAGS: " + "; ".join(flags))
    if notes_parts:
        rec["notes"] = " | ".join(p for p in notes_parts if p)

    return rec


def validate_all(input_path: Path, output_path: Path, workers: int = 8,
                 check_liveness: bool = True) -> int:
    records = read_jsonl(input_path)
    log.info("Validating %d records", len(records))

    session = make_session() if check_liveness else None
    url_cache: dict[str, bool] = {}

    if check_liveness and session:
        # Validate in parallel, but the url_cache makes most calls free
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(
                lambda r: validate_one(r, session, url_cache), records
            ))
    else:
        results = [validate_one(r, None, url_cache) for r in records]  # type: ignore

    write_jsonl(output_path, results)
    log.info("Validation complete")

    # Summary stats
    flagged = sum(1 for r in results if r.get("notes", "").find("FLAGS:") >= 0)
    avg_conf = sum(r.get("confidence_score") or 0 for r in results) / len(results)
    log.info("  Flagged records: %d (%.1f%%)", flagged, 100 * flagged / len(results))
    log.info("  Mean confidence: %.3f", avg_conf)
    return len(results)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate deduped records")
    parser.add_argument("--input", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/processed/deduped.jsonl")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/processed/validated.jsonl")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-liveness", action="store_true",
                        help="Skip HTTP liveness checks (much faster)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    validate_all(args.input, args.output, workers=args.workers,
                 check_liveness=not args.no_liveness)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
