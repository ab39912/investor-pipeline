"""
Final exporter — emit the deliverable CSV + JSON.

Reads the validated JSONL, applies a final quality filter (drop records
with critical mandatory fields still missing AND low confidence), sorts
by confidence_score desc, optionally caps to N rows, writes both formats.

The CSV is what most evaluators will open first, so column order matters:
mandatory fields first, then nice-to-haves, then metadata.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.schema import ALL_FIELDS, MANDATORY_FIELDS  # noqa: E402
from collectors.utils import read_jsonl  # noqa: E402

log = logging.getLogger(__name__)


def quality_filter(rec: dict, min_conf: float = 0.3) -> bool:
    """Keep records meeting minimum quality bar."""
    # Absolute essentials: name + source attribution
    if not rec.get("investor_name") or not rec.get("source_url"):
        return False
    if not rec.get("data_source") or not rec.get("date_collected"):
        return False
    conf = rec.get("confidence_score") or 0
    if conf < min_conf:
        return False
    return True


def export(input_path: Path, csv_path: Path, json_path: Path,
           limit: int = 2000, min_conf: float = 0.3) -> int:
    records = read_jsonl(input_path)
    log.info("Loaded %d validated records", len(records))

    filtered = [r for r in records if quality_filter(r, min_conf)]
    log.info("After quality filter (min_conf=%.2f): %d", min_conf, len(filtered))

    # Sort highest confidence first
    filtered.sort(key=lambda r: r.get("confidence_score") or 0, reverse=True)

    if limit and len(filtered) > limit:
        filtered = filtered[:limit]
        log.info("Capped to top %d by confidence", limit)

    # Ensure every record has every field key (CSV needs consistent columns)
    for r in filtered:
        for k in ALL_FIELDS:
            r.setdefault(k, None)

    # CSV — mandatory fields first
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ALL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in filtered:
            writer.writerow({k: ("" if r.get(k) is None else r[k]) for k in ALL_FIELDS})
    log.info("Wrote CSV: %s", csv_path)

    # JSON — same data
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)
    log.info("Wrote JSON: %s", json_path)

    # Summary stats for the methodology doc
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_country: dict[str, int] = {}
    for r in filtered:
        for src in (r.get("data_source") or "unknown").split(";"):
            src = src.strip()
            by_source[src] = by_source.get(src, 0) + 1
        by_type[r.get("investor_type") or "unknown"] = \
            by_type.get(r.get("investor_type") or "unknown", 0) + 1
        c = r.get("country") or "unknown"
        by_country[c] = by_country.get(c, 0) + 1

    avg_conf = sum(r.get("confidence_score") or 0 for r in filtered) / max(len(filtered), 1)
    mandatory_complete = sum(
        1 for r in filtered if all(r.get(k) for k in MANDATORY_FIELDS)
    )

    stats = {
        "total_records": len(filtered),
        "avg_confidence": round(avg_conf, 3),
        "fully_complete_mandatory": mandatory_complete,
        "by_data_source": dict(sorted(by_source.items(), key=lambda x: -x[1])),
        "by_investor_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "top_countries": dict(sorted(by_country.items(), key=lambda x: -x[1])[:15]),
    }
    stats_path = csv_path.with_suffix(".stats.json")
    with stats_path.open("w") as f:
        json.dump(stats, f, indent=2)
    log.info("Wrote stats: %s", stats_path)
    log.info("Summary: %s", json.dumps(stats, indent=2))

    return len(filtered)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export final dataset")
    parser.add_argument("--input", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/processed/validated.jsonl")
    parser.add_argument("--csv", type=Path,
                        default=Path(__file__).resolve().parents[1] / "output/Investor_Dataset.csv")
    parser.add_argument("--json", type=Path,
                        default=Path(__file__).resolve().parents[1] / "output/Investor_Dataset.json")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--min-conf", type=float, default=0.3)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    export(args.input, args.csv, args.json, limit=args.limit, min_conf=args.min_conf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
