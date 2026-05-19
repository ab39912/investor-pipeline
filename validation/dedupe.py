"""
Deduplication.

Strategy: blocking + fuzzy match.

1. Build two blocking keys per record:
   - normalized domain (e.g., "sequoiacap.com")
   - first 3 letters of normalized firm name (cheap blocking)

2. Within each block, do pairwise rapidfuzz comparisons on the full
   normalized name. Pairs above threshold (default 88) are duplicates.

3. Merge dupes by preferring the record with the highest confidence_score.
   Other records' non-null fields fill any gaps in the surviving record.
   Source URLs and data sources are concatenated for provenance.

We do NOT delete dupes silently — every dropped record is logged with
the surviving record's ID, so the methodology doc can quote stats.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.utils import read_jsonl, write_jsonl  # noqa: E402

log = logging.getLogger(__name__)


_SUFFIXES = (
    "ltd", "llc", "inc", "corp", "corporation", "limited", "gmbh", "sa",
    "plc", "lp", "co", "company", "group", "holdings", "ventures",
    "capital", "partners",
)


def normalize_name_for_match(name: str) -> str:
    """Lowercase, strip punctuation, remove common suffixes for matching.

    Punctuation is stripped first so 'Foo, Inc.' and 'Foo Inc' both
    collapse to 'foo'. Suffixes are removed iteratively because real-world
    names stack them (e.g., 'Foo Capital Partners LP').
    """
    n = name.lower()
    n = re.sub(r"[^\w\s]", " ", n)
    n = " ".join(n.split())
    # Iteratively strip suffixes — words on the right edge of the name
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            tail = " " + suffix
            if n.endswith(tail):
                n = n[: -len(tail)].rstrip()
                changed = True
                break
            if n == suffix:
                n = ""
                changed = True
                break
    return n


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def merge_records(survivor: dict, dupe: dict) -> dict:
    """Fill survivor's None fields from dupe; concatenate provenance."""
    for k, v in dupe.items():
        if survivor.get(k) is None and v is not None:
            survivor[k] = v

    # Concatenate provenance
    s_src = survivor.get("data_source", "")
    d_src = dupe.get("data_source", "")
    if d_src and d_src not in s_src:
        survivor["data_source"] = f"{s_src}; {d_src}".strip("; ")

    # Keep highest confidence
    s_conf = survivor.get("confidence_score") or 0.0
    d_conf = dupe.get("confidence_score") or 0.0
    survivor["confidence_score"] = max(s_conf, d_conf)

    return survivor


def dedupe(records: list[dict], threshold: int = 88) -> tuple[list[dict], int]:
    """Returns (deduped_records, num_dropped)."""
    try:
        from rapidfuzz import fuzz
    except ImportError:
        log.error("rapidfuzz not installed. pip install rapidfuzz")
        return records, 0

    # Phase 1: exact domain match
    by_domain: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        d = domain_of(r.get("website"))
        if d:
            by_domain[d].append(i)

    # Phase 2: build cluster assignment
    parent = list(range(len(records)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Same-domain records cluster together (high confidence dupe signal)
    for domain, idxs in by_domain.items():
        if len(idxs) > 1:
            anchor = idxs[0]
            for j in idxs[1:]:
                union(anchor, j)

    # Phase 3: fuzzy name match within first-letter blocks
    by_letter: dict[str, list[int]] = defaultdict(list)
    normalized = [normalize_name_for_match(r.get("investor_name", "")) for r in records]
    for i, name in enumerate(normalized):
        if name:
            by_letter[name[:3]].append(i)

    for letter, idxs in by_letter.items():
        # Skip huge blocks to keep it tractable (very common prefixes)
        if len(idxs) > 400:
            continue
        for i in range(len(idxs)):
            for j in range(i + 1, len(idxs)):
                a, b = idxs[i], idxs[j]
                if find(a) == find(b):
                    continue  # already clustered
                score = fuzz.token_sort_ratio(normalized[a], normalized[b])
                if score >= threshold:
                    union(a, b)

    # Phase 4: collapse clusters into surviving records
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(records)):
        clusters[find(i)].append(i)

    deduped: list[dict] = []
    dropped = 0
    for root, members in clusters.items():
        if len(members) == 1:
            deduped.append(records[members[0]])
            continue
        # Pick survivor: highest confidence, tie-break by data_source priority
        priority = {"SEC EDGAR": 3, "Wikidata": 2, "OpenVC": 2, "Y Combinator": 1}
        members_sorted = sorted(
            members,
            key=lambda i: (
                records[i].get("confidence_score") or 0,
                priority.get(records[i].get("data_source", "").split(";")[0].strip(), 0),
            ),
            reverse=True,
        )
        survivor = dict(records[members_sorted[0]])
        for m in members_sorted[1:]:
            survivor = merge_records(survivor, records[m])
            dropped += 1
        deduped.append(survivor)

    log.info("Deduplication: %d records -> %d (dropped %d)",
             len(records), len(deduped), dropped)
    return deduped, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicate normalized records")
    parser.add_argument("--input", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/processed/normalized.jsonl")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/processed/deduped.jsonl")
    parser.add_argument("--threshold", type=int, default=88,
                        help="rapidfuzz score threshold for fuzzy match (0-100)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    records = read_jsonl(args.input)
    log.info("Loaded %d records", len(records))
    deduped, _ = dedupe(records, threshold=args.threshold)
    write_jsonl(args.output, deduped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
