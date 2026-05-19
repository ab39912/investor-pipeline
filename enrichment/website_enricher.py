"""
Firm website enricher — fetch each investor's homepage + About page,
extract a clean text snippet for downstream AI thesis/sector classification.

This is the "enrichment" pass mentioned in the methodology. It does NOT
make up data; it only stores raw text from the firm's own website, with
the source URL recorded. The AI module turns that text into structured
fields (investment_thesis, sector_focus refinement) in a separate step.

Key behaviors:
- Honors robots.txt per host (one check per host, cached)
- Caps text to ~3000 chars per firm to keep AI costs sane
- Skips firms without a website
- Recovers from individual failures; doesn't crash the batch
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.utils import Throttle, make_session, safe_text  # noqa: E402

log = logging.getLogger(__name__)

# Pages we'll look for at each firm's domain root
ABOUT_PATHS = ("/about", "/about-us", "/team", "/investment-thesis", "/thesis",
               "/approach", "/who-we-are", "/our-firm", "/")

MAX_TEXT_LEN = 3000  # chars of raw text per firm
TIMEOUT = 15


class RobotsCache:
    """Per-host robots.txt cache. Honors disallow directives."""

    def __init__(self) -> None:
        self._cache: dict[str, RobotFileParser] = {}

    def allowed(self, url: str, user_agent: str) -> bool:
        host = urlparse(url).netloc
        if not host:
            return False
        if host not in self._cache:
            rp = RobotFileParser()
            rp.set_url(f"{urlparse(url).scheme}://{host}/robots.txt")
            try:
                rp.read()
            except Exception:  # noqa: BLE001
                # If robots.txt is unreachable, default to *not* fetching.
                # Conservative; protects us from accidentally hammering a
                # site that prohibits scraping.
                log.debug("robots.txt unreachable for %s — skipping", host)
                self._cache[host] = None  # type: ignore[assignment]
                return False
            self._cache[host] = rp
        rp = self._cache[host]
        if rp is None:
            return False
        return rp.can_fetch(user_agent, url)


def extract_text(html: str) -> str:
    """Pull readable text from a page. Strips nav/script/style."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(("script", "style", "nav", "footer", "header", "form")):
        tag.decompose()
    # Prefer main / article tags when present
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = " ".join(main.stripped_strings)
    return safe_text(text) or ""


def fetch_one(session: requests.Session, url: str, throttle: Throttle,
              robots: RobotsCache, ua: str) -> str | None:
    if not robots.allowed(url, ua):
        log.debug("robots.txt disallows %s", url)
        return None
    throttle.wait()
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            return None
        return r.text
    except Exception as e:  # noqa: BLE001
        log.debug("Fetch failed for %s: %s", url, e)
        return None


def enrich_one_firm(session: requests.Session, website: str, throttle: Throttle,
                    robots: RobotsCache, ua: str) -> dict | None:
    """Try the homepage and a couple of About-like paths; return collected text."""
    if not website:
        return None
    if not website.startswith("http"):
        website = "https://" + website

    collected_text: list[str] = []
    sources_tried: list[str] = []

    # Homepage first
    home_html = fetch_one(session, website, throttle, robots, ua)
    if home_html:
        sources_tried.append(website)
        collected_text.append(extract_text(home_html))

    # Try one About-like path. Don't hammer with all of them.
    for path in ABOUT_PATHS[:3]:
        if path == "/":
            continue
        candidate = urljoin(website, path)
        html = fetch_one(session, candidate, throttle, robots, ua)
        if html:
            sources_tried.append(candidate)
            collected_text.append(extract_text(html))
            break

    if not collected_text:
        return None

    combined = " ".join(t for t in collected_text if t)
    return {
        "website": website,
        "sources_fetched": sources_tried,
        "raw_text": combined[:MAX_TEXT_LEN],
        "text_length": len(combined),
    }


def enrich_file(input_path: Path, output_path: Path, limit: int | None = None) -> int:
    """Enrich every record in input_path that has a website. Writes a
    side-file mapping website -> raw_text for the AI step to consume."""
    from collectors.utils import USER_AGENT, read_jsonl

    records = read_jsonl(input_path)
    log.info("Loaded %d records from %s", len(records), input_path)

    # Unique websites only — many records share a domain (e.g., same firm,
    # different partners)
    seen_websites: set[str] = set()
    targets: list[str] = []
    for r in records:
        w = r.get("website")
        if not w:
            continue
        key = urlparse(w if w.startswith("http") else "https://" + w).netloc.lower()
        if key and key not in seen_websites:
            seen_websites.add(key)
            targets.append(w)

    if limit:
        targets = targets[:limit]
    log.info("Unique websites to enrich: %d", len(targets))

    session = make_session()
    throttle = Throttle(delay=1.0)
    robots = RobotsCache()

    enriched: dict[str, dict] = {}
    for i, website in enumerate(targets, 1):
        result = enrich_one_firm(session, website, throttle, robots, USER_AGENT)
        if result:
            enriched[website] = result
        if i % 25 == 0:
            log.info("  %d/%d processed, %d enriched", i, len(targets), len(enriched))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)

    log.info("Enrichment complete: %d websites yielded text", len(enriched))
    return len(enriched)


def main() -> int:
    parser = argparse.ArgumentParser(description="Firm website enricher")
    parser.add_argument("--input", type=Path, required=True,
                        help="JSONL file of records with website fields")
    parser.add_argument("--output", type=Path, required=True,
                        help="JSON file to write enrichment side-data")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    n = enrich_file(args.input, args.output, args.limit)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
