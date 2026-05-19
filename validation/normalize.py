"""
Normalizer — read all collector outputs, unify to one schema, clean values.

Operations:
  - Merge every JSONL in data/raw/ into one big list
  - Normalize country names to ISO short names (pycountry)
  - Normalize URLs (lowercase host, strip trailing slash, drop tracking params)
  - Strip and collapse whitespace everywhere
  - Map investor_type variants to controlled vocabulary
  - Fill date_collected if missing (today)
  - Ensure all mandatory keys exist (set to None if absent)

The dedup step runs AFTER normalization so we're comparing apples to apples.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.schema import ALL_FIELDS, INVESTOR_TYPES, MANDATORY_FIELDS  # noqa: E402
from collectors.utils import read_jsonl, safe_text, write_jsonl  # noqa: E402

log = logging.getLogger(__name__)


def normalize_country(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    # Quick path for common variants
    aliases = {
        "USA": "United States", "U.S.": "United States",
        "U.S.A.": "United States", "US": "United States",
        "UK": "United Kingdom", "U.K.": "United Kingdom",
        "England": "United Kingdom", "Great Britain": "United Kingdom",
        "UAE": "United Arab Emirates",
    }
    if raw in aliases:
        return aliases[raw]
    try:
        import pycountry
        # Try by name, then by alpha_2/alpha_3
        for attr in ("name", "alpha_2", "alpha_3", "official_name"):
            try:
                c = pycountry.countries.lookup(raw)
                return c.name
            except LookupError:
                continue
    except ImportError:
        pass
    return raw  # leave as-is if we can't resolve


def normalize_url(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Add scheme if missing
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        p = urlparse(raw)
        if not p.netloc:
            return None
        # Lowercase host, strip default ports, drop fragment+query for canonical
        netloc = p.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = p.path.rstrip("/")
        return urlunparse((p.scheme, netloc, path, "", "", ""))
    except Exception:
        return raw


def normalize_investor_type(raw: str | None) -> str:
    if not raw:
        return "other"
    r = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if r in INVESTOR_TYPES:
        return r
    # Common aliases
    aliases = {
        "vc": "venture_capital",
        "venture": "venture_capital",
        "venture_capital_firm": "venture_capital",
        "pe": "private_equity",
        "private_equity_firm": "private_equity",
        "family_offices": "family_office",
        "cvc": "corporate_venture",
        "corporate_venture_capital": "corporate_venture",
        "lp": "limited_partner",
        "startup_fund": "startup_fund",
    }
    return aliases.get(r, "other")


def normalize_record(rec: dict) -> dict:
    """Apply all normalizations in place; return cleaned record."""
    out: dict = {}

    # Pass 1: copy with whitespace cleaning
    for k in ALL_FIELDS:
        v = rec.get(k)
        if isinstance(v, str):
            v = safe_text(v)
        out[k] = v

    # Pass 2: specific fields
    out["investor_type"] = normalize_investor_type(out.get("investor_type"))
    out["country"] = normalize_country(out.get("country"))
    out["website"] = normalize_url(out.get("website"))
    out["source_url"] = normalize_url(out.get("source_url")) or rec.get("source_url")
    out["linkedin_url"] = normalize_url(out.get("linkedin_url"))

    if not out.get("date_collected"):
        out["date_collected"] = date.today().isoformat()

    # If firm_name is missing, fall back to investor_name (common for VC firms
    # where the firm IS the investor)
    if not out.get("firm_name"):
        out["firm_name"] = out.get("investor_name")

    return out


def normalize_all(raw_dir: Path, output_path: Path) -> int:
    """Read every *.jsonl in raw_dir, normalize, write merged output."""
    files = sorted(raw_dir.glob("*.jsonl"))
    log.info("Normalizing %d raw files", len(files))

    merged: list[dict] = []
    for f in files:
        recs = read_jsonl(f)
        log.info("  %s -> %d records", f.name, len(recs))
        for r in recs:
            normalized = normalize_record(r)
            # Drop records missing the absolute minimum: a name AND a source
            if not normalized.get("investor_name") or not normalized.get("source_url"):
                continue
            merged.append(normalized)

    write_jsonl(output_path, merged)
    log.info("Normalized total: %d records -> %s", len(merged), output_path)
    return len(merged)


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize all raw collector outputs")
    parser.add_argument("--raw-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/raw")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/processed/normalized.jsonl")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    n = normalize_all(args.raw_dir, args.out)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
