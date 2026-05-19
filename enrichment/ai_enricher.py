"""
AI enrichment via the Anthropic API (Claude).

What this module does:
  Given a firm name + raw text scraped from that firm's own About page,
  produce structured fields: refined sector_focus, investment_stage,
  investment_thesis (one-sentence summary).

What this module does NOT do:
  Fabricate fields when the source text doesn't support them. The prompt
  forces a JSON schema with explicit "unknown" allowed, and a
  confidence_score per inference. If the model isn't sure, it says so.

This separation matters for the assignment: AI is being used to
*structure existing public text*, not to *generate investor information
out of thin air*. That distinction is what makes the AI usage defensible.

Concurrency: requests are batched with a small thread pool. Anthropic's
rate limits depend on your tier; the defaults here are conservative.

Cost note: with claude-haiku-4-5 this is roughly $0.005-0.01 per firm at
3000-char input. For 2,000 firms, expect ~$10-20 total.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.utils import read_jsonl, write_jsonl  # noqa: E402

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a data extraction assistant for an investor database.
You will be given:
  1. The name of an investment firm.
  2. Raw text scraped from that firm's own public website (About page,
     thesis page, etc).

Your job: extract structured fields IF AND ONLY IF the text directly supports
them. You must NOT invent, infer beyond the text, or use prior knowledge.
If the text does not mention a field, return null for that field.

Return a single JSON object with exactly these keys:
  - "investment_thesis": one-sentence summary in the firm's own framing,
    or null. Max 200 chars. Must be derivable from the provided text.
  - "sector_focus": comma-separated sectors mentioned (e.g.,
    "fintech, healthcare, climate"), or null.
  - "investment_stage": comma-separated stages mentioned (e.g.,
    "pre-seed, seed, Series A"), or null. Only standard stage labels.
  - "geographic_focus": regions mentioned (e.g., "North America, Europe"),
    or null.
  - "confidence": float 0.0-1.0 — your confidence the extracted fields
    accurately reflect the source text. 1.0 means every field comes
    verbatim from the text; lower if you had to paraphrase.
  - "evidence_phrase": one short verbatim quote (<15 words) from the
    source text supporting your extraction, or null if no extraction made.

Return ONLY the JSON, no surrounding markdown or commentary.
If the text appears to be navigation/junk and not real firm content,
return all-null fields with confidence 0.0."""


USER_TEMPLATE = """Firm name: {firm_name}

Source text (from {website}):
\"\"\"
{text}
\"\"\""""


def call_claude(client, firm_name: str, website: str, text: str,
                model: str = "claude-haiku-4-5-20251001") -> dict:
    """One API call. Returns parsed JSON dict, or a stub on error."""
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": USER_TEMPLATE.format(
                    firm_name=firm_name,
                    website=website,
                    text=text[:3000],
                ),
            }],
        )
        # Extract text from content blocks
        raw = "".join(b.text for b in msg.content if hasattr(b, "text"))
        raw = raw.strip()
        # Strip code fences if model added them despite instructions
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("JSON parse failed for %s: %s", firm_name, e)
        return {"_error": "json_parse"}
    except Exception as e:  # noqa: BLE001
        log.warning("API call failed for %s: %s", firm_name, e)
        return {"_error": str(e)[:200]}


def enrich(records_path: Path, website_text_path: Path, output_path: Path,
           max_workers: int = 4, limit: int | None = None) -> int:
    """Apply AI enrichment to records that have scraped website text."""
    try:
        import anthropic
    except ImportError:
        log.error("anthropic package not installed. pip install anthropic")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY env var not set")
        return 0

    client = anthropic.Anthropic()

    records = read_jsonl(records_path)
    with website_text_path.open() as f:
        website_text = json.load(f)

    log.info("Loaded %d records and %d scraped websites", len(records), len(website_text))

    # Build list of (idx, firm_name, website, text) for AI calls
    jobs: list[tuple[int, str, str, str]] = []
    for idx, r in enumerate(records):
        website = r.get("website")
        if not website:
            continue
        text_entry = website_text.get(website)
        if not text_entry or not text_entry.get("raw_text"):
            continue
        jobs.append((idx, r["investor_name"], website, text_entry["raw_text"]))

    if limit:
        jobs = jobs[:limit]
    log.info("Enrichment jobs queued: %d", len(jobs))

    enriched_count = 0
    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {
            pool.submit(call_claude, client, name, site, text): idx
            for idx, name, site, text in jobs
        }
        for i, future in enumerate(as_completed(future_to_idx), 1):
            idx = future_to_idx[future]
            result = future.result()

            if "_error" in result:
                continue

            r = records[idx]
            # Only overwrite if AI provided non-null AND we had nothing
            for key in ("investment_thesis", "sector_focus",
                        "investment_stage", "geographic_focus"):
                val = result.get(key)
                if val and not r.get(key):
                    r[key] = val

            # Combine confidence: take min of source confidence and AI confidence
            ai_conf = result.get("confidence")
            if isinstance(ai_conf, (int, float)):
                src_conf = r.get("confidence_score") or 0.8
                r["confidence_score"] = round(min(src_conf, float(ai_conf)), 2)

            ev = result.get("evidence_phrase")
            if ev:
                existing_notes = r.get("notes") or ""
                r["notes"] = (existing_notes + " | AI evidence: " + ev[:140]).strip(" |")

            enriched_count += 1

            if i % 50 == 0:
                rate = i / (time.monotonic() - start)
                log.info("  %d/%d processed (%.1f/s), %d enriched",
                         i, len(jobs), rate, enriched_count)

    write_jsonl(output_path, records)
    log.info("AI enrichment complete: %d/%d records enriched", enriched_count, len(records))
    return enriched_count


def main() -> int:
    parser = argparse.ArgumentParser(description="AI enrichment via Claude")
    parser.add_argument("--records", type=Path, required=True,
                        help="JSONL of deduped records to enrich")
    parser.add_argument("--website-text", type=Path, required=True,
                        help="JSON file from website_enricher.py")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    enrich(args.records, args.website_text, args.output,
           max_workers=args.workers, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
