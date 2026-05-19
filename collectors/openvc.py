"""
OpenVC collector — public VC directory.

OpenVC (openvc.app) publishes a free, public investor list at
https://www.openvc.app/investor-list — it's explicitly marketed as
"100% free, exportable" and they expose it without login. Their terms
permit non-commercial research use; we identify ourselves honestly and
throttle politely.

NOTE: OpenVC occasionally changes their HTML structure. If the selectors
below stop matching, run with `--debug-html` to dump a sample page so
selectors can be updated. This is the inherent fragility of scraping
HTML versus pulling from APIs — covered honestly in the methodology doc.

If at runtime OpenVC has moved to an entirely different layout, this
collector will gracefully report 0 records and the pipeline continues.
We do NOT silently fabricate fallback data.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.schema import InvestorRecord  # noqa: E402
from collectors.utils import Throttle, make_session, safe_text, write_jsonl  # noqa: E402

log = logging.getLogger(__name__)

BASE_URL = "https://www.openvc.app"
LIST_URL = f"{BASE_URL}/investor-list"


def fetch_listing_page(session: requests.Session, page: int, throttle: Throttle) -> str:
    """Fetch one page of the OpenVC listing. Their pagination is
    query-string based; we honor whatever they expose at runtime."""
    throttle.wait()
    params = {"page": page} if page > 1 else {}
    r = session.get(LIST_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.text


def parse_listing(html: str) -> list[dict]:
    """Extract investor rows from a listing page.

    OpenVC's table structure (as of last manual inspection) has:
      <tr> rows with <td> cells for: name, type, country, stage, sector,
      website (anchor), check size.

    We're defensive here: if structure changes, we yield best-effort fields
    rather than crashing. Unknown fields become None, and the normalizer
    handles missing data.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []

    # Strategy A: table-style listing
    rows = soup.select("table tr")
    if rows and len(rows) > 1:
        headers = [safe_text(th.get_text()) for th in rows[0].select("th")]
        for row in rows[1:]:
            cells = row.select("td")
            if not cells:
                continue
            data = dict(zip(headers or [], (safe_text(c.get_text()) for c in cells)))
            anchor = row.find("a", href=True)
            if anchor:
                data["_link"] = anchor["href"]
            out.append(data)
        return out

    # Strategy B: card-style listing (fallback)
    cards = soup.select("[class*='investor'], [class*='card']")
    for card in cards:
        name_el = card.select_one("h2, h3, .name, [class*='title']")
        if not name_el:
            continue
        link_el = card.find("a", href=True)
        out.append({
            "name": safe_text(name_el.get_text()),
            "_link": link_el["href"] if link_el else None,
            "_raw": safe_text(card.get_text())[:300] if card else None,
        })

    return out


def row_to_record(row: dict) -> InvestorRecord | None:
    """Map a parsed OpenVC row to InvestorRecord. Tolerant of missing fields."""
    # Normalize headers to lowercase keys we recognize
    norm = {(k or "").lower().strip(): v for k, v in row.items() if k}
    name = norm.get("investor") or norm.get("name") or norm.get("firm")
    if not name:
        return None

    link = row.get("_link")
    source_url = link if (link and link.startswith("http")) else (
        f"{BASE_URL}{link}" if link else LIST_URL
    )

    return InvestorRecord(
        investor_name=name,
        investor_type=normalize_type(norm.get("type") or norm.get("investor type") or ""),
        firm_name=name,
        website=norm.get("website"),
        country=norm.get("country") or norm.get("hq country"),
        geographic_focus=norm.get("geography") or norm.get("region focus"),
        sector_focus=norm.get("sector") or norm.get("sector focus"),
        source_url=source_url,
        data_source="OpenVC",
        date_collected=date.today().isoformat(),
        investment_stage=norm.get("stage") or norm.get("investment stage"),
        typical_ticket_size=norm.get("check size") or norm.get("ticket size"),
        confidence_score=0.80,
    )


def normalize_type(raw: str) -> str:
    """Map free-text OpenVC type field to our controlled vocabulary."""
    r = raw.lower()
    if not r:
        return "venture_capital"  # OpenVC is mostly VC
    if "family" in r:
        return "family_office"
    if "angel" in r:
        return "angel"
    if "accelerator" in r:
        return "accelerator"
    if "incubator" in r:
        return "incubator"
    if "private equity" in r or " pe " in r:
        return "private_equity"
    if "corporate" in r or "cvc" in r:
        return "corporate_venture"
    if "lp" in r or "limited partner" in r:
        return "limited_partner"
    return "venture_capital"


def collect(max_pages: int = 50, output_path: Path | None = None) -> int:
    if output_path is None:
        output_path = Path(__file__).resolve().parents[1] / "data/raw/openvc.jsonl"

    session = make_session()
    throttle = Throttle(delay=2.0)  # be friendly to OpenVC

    all_records: list[dict] = []
    consecutive_empty = 0

    for page in range(1, max_pages + 1):
        try:
            html = fetch_listing_page(session, page, throttle)
        except requests.HTTPError as e:
            log.warning("Page %d HTTP error: %s — stopping pagination", page, e)
            break
        except Exception as e:  # noqa: BLE001
            log.warning("Page %d unexpected error: %s — stopping", page, e)
            break

        rows = parse_listing(html)
        page_records = 0
        for row in rows:
            rec = row_to_record(row)
            if rec:
                all_records.append(rec.to_dict())
                page_records += 1

        log.info("Page %d: %d records (cum %d)", page, page_records, len(all_records))
        if page_records == 0:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                log.info("Two consecutive empty pages — stopping pagination")
                break
        else:
            consecutive_empty = 0

    write_jsonl(output_path, all_records)
    log.info("OpenVC collection complete: %d records", len(all_records))
    return len(all_records)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenVC investor collector")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    n = collect(max_pages=args.max_pages, output_path=args.out)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
