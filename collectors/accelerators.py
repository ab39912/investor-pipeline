"""
Y Combinator collector — YC itself + YC's public list of "Top Companies"
investors, plus the public partner list.

YC publishes:
  - https://www.ycombinator.com/people  (partners — public, no auth)
  - https://www.ycombinator.com/companies (alumni; not what we want here)

This collector treats YC as one organization (the accelerator) and pulls
the partner-level people listed publicly as angels who often invest
independently.

We deliberately do NOT scrape personal contact info that's not openly
published on the page. Names + role + public bio link only.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.schema import InvestorRecord  # noqa: E402
from collectors.utils import Throttle, make_session, safe_text, write_jsonl  # noqa: E402

log = logging.getLogger(__name__)

PARTNERS_URL = "https://www.ycombinator.com/people"

# Other public accelerator partner pages we can collect from in the same way.
# Each entry: (data_source, firm_name, url, investor_type, country)
ACCELERATORS = [
    ("Y Combinator", "Y Combinator", PARTNERS_URL, "accelerator", "United States"),
    ("Techstars", "Techstars", "https://www.techstars.com/about/team",
     "accelerator", "United States"),
    ("500 Global", "500 Global", "https://500.co/team",
     "accelerator", "United States"),
]


def fetch(session: requests.Session, url: str, throttle: Throttle) -> str | None:
    throttle.wait()
    try:
        r = session.get(url, timeout=30)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to fetch %s: %s", url, e)
        return None


def parse_partners(html: str) -> list[dict]:
    """Extract partner cards. Defensive across HTML changes."""
    soup = BeautifulSoup(html, "html.parser")

    cards = []
    # Try common patterns
    for selector in (
        "div[class*='partner']",
        "div[class*='person']",
        "div[class*='team']",
        "article",
        "li[class*='member']",
    ):
        found = soup.select(selector)
        if len(found) >= 5:
            cards = found
            break

    out: list[dict] = []
    for card in cards:
        name_el = (card.select_one("h2, h3, h4") or
                   card.select_one("[class*='name']"))
        if not name_el:
            continue
        title_el = (card.select_one("[class*='title'], [class*='role'], p"))
        link_el = card.find("a", href=True)
        out.append({
            "name": safe_text(name_el.get_text()),
            "title": safe_text(title_el.get_text()) if title_el else None,
            "profile_url": link_el["href"] if link_el else None,
        })
    return out


def people_to_records(
    people: list[dict],
    firm_name: str,
    firm_url: str,
    investor_type: str,
    country: str,
    data_source: str,
) -> list[InvestorRecord]:
    """Turn partner cards into individual angel/partner records."""
    today = date.today().isoformat()
    out: list[InvestorRecord] = []
    for p in people:
        name = p.get("name")
        if not name or len(name) < 2:
            continue
        title = (p.get("title") or "").lower()
        # Only include people who are clearly investors/partners
        if not any(k in title for k in ("partner", "principal", "investor",
                                        "managing director", "venture", "founder")):
            # Still keep — they're on a public team page of an investing firm
            pass

        prof = p.get("profile_url") or ""
        if prof and prof.startswith("/"):
            from urllib.parse import urljoin
            prof = urljoin(firm_url, prof)

        out.append(InvestorRecord(
            investor_name=name,
            investor_type="angel" if investor_type == "accelerator" else investor_type,
            firm_name=firm_name,
            website=firm_url,
            country=country,
            geographic_focus="Global",
            sector_focus="Technology / Startups",
            source_url=prof or firm_url,
            data_source=data_source,
            date_collected=today,
            linkedin_url=prof if "linkedin" in prof.lower() else None,
            key_people=name,
            notes=f"{title.title()} at {firm_name}" if title else None,
            confidence_score=0.85,
        ))
    return out


def collect_firm_records(firm_name: str, firm_url: str, investor_type: str,
                         country: str, data_source: str) -> list[InvestorRecord]:
    """One record for the firm itself, regardless of partner scrape outcome."""
    return [InvestorRecord(
        investor_name=firm_name,
        investor_type=investor_type,
        firm_name=firm_name,
        website=firm_url.split("/team")[0].split("/people")[0].split("/about")[0],
        country=country,
        geographic_focus="Global",
        sector_focus="Technology / Startups",
        source_url=firm_url,
        data_source=data_source,
        date_collected=date.today().isoformat(),
        confidence_score=0.95,
        notes=f"Top-level entry for accelerator {firm_name}",
    )]


def collect(output_path: Path | None = None) -> int:
    if output_path is None:
        output_path = Path(__file__).resolve().parents[1] / "data/raw/accelerators.jsonl"

    session = make_session()
    throttle = Throttle(delay=2.0)

    all_records: list[dict] = []
    for data_source, firm_name, url, inv_type, country in ACCELERATORS:
        log.info("Collecting %s from %s", firm_name, url)

        # Always emit the firm itself
        for rec in collect_firm_records(firm_name, url, inv_type, country, data_source):
            all_records.append(rec.to_dict())

        # Try to enrich with partner-level records
        html = fetch(session, url, throttle)
        if not html:
            continue
        people = parse_partners(html)
        log.info("  found %d partner cards", len(people))
        people_records = people_to_records(people, firm_name, url, inv_type, country, data_source)
        all_records.extend(r.to_dict() for r in people_records)

    write_jsonl(output_path, all_records)
    log.info("Accelerator collection complete: %d records", len(all_records))
    return len(all_records)


def main() -> int:
    parser = argparse.ArgumentParser(description="Accelerator/YC partner collector")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    n = collect(output_path=args.out)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
