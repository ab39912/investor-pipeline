"""
Wikidata collector — globally diverse VC firms, PE firms, and family offices.

Why Wikidata:
- Free public SPARQL endpoint, no auth, no rate limit drama
- Already-curated structured data (founded date, country, HQ, employees,
  parent company, official website)
- Genuinely global — fills gaps that SEC EDGAR (US-only) misses

Approach: one SPARQL query per investor type. Each query asks Wikidata for
entities that are instances of (or subclasses of) the relevant concept,
joined with country and website where present.

Useful Wikidata IDs we lean on:
  Q1335617 = venture capital firm
  Q3622547 = private equity firm
  Q3299324 = family office
  Q524656  = investment company
  Q2502882 = startup accelerator
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.schema import InvestorRecord  # noqa: E402
from collectors.utils import Throttle, make_session, write_jsonl  # noqa: E402

log = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# One query per Wikidata class -> our investor_type vocabulary.
# LIMIT is set high; we paginate by class and country if needed.
QUERIES = {
    "venture_capital": """
        SELECT DISTINCT ?firm ?firmLabel ?countryLabel ?website ?hqLabel ?inception WHERE {
          ?firm wdt:P31/wdt:P279* wd:Q1335617 .
          OPTIONAL { ?firm wdt:P17 ?country . }
          OPTIONAL { ?firm wdt:P856 ?website . }
          OPTIONAL { ?firm wdt:P159 ?hq . }
          OPTIONAL { ?firm wdt:P571 ?inception . }
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        LIMIT 1500
    """,
    "private_equity": """
        SELECT DISTINCT ?firm ?firmLabel ?countryLabel ?website ?hqLabel ?inception WHERE {
          ?firm wdt:P31/wdt:P279* wd:Q3622547 .
          OPTIONAL { ?firm wdt:P17 ?country . }
          OPTIONAL { ?firm wdt:P856 ?website . }
          OPTIONAL { ?firm wdt:P159 ?hq . }
          OPTIONAL { ?firm wdt:P571 ?inception . }
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        LIMIT 1000
    """,
    "family_office": """
        SELECT DISTINCT ?firm ?firmLabel ?countryLabel ?website ?hqLabel WHERE {
          ?firm wdt:P31/wdt:P279* wd:Q3299324 .
          OPTIONAL { ?firm wdt:P17 ?country . }
          OPTIONAL { ?firm wdt:P856 ?website . }
          OPTIONAL { ?firm wdt:P159 ?hq . }
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        LIMIT 500
    """,
    "accelerator": """
        SELECT DISTINCT ?firm ?firmLabel ?countryLabel ?website ?hqLabel ?inception WHERE {
          ?firm wdt:P31/wdt:P279* wd:Q2502882 .
          OPTIONAL { ?firm wdt:P17 ?country . }
          OPTIONAL { ?firm wdt:P856 ?website . }
          OPTIONAL { ?firm wdt:P159 ?hq . }
          OPTIONAL { ?firm wdt:P571 ?inception . }
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        LIMIT 500
    """,
}


def run_query(session: requests.Session, sparql: str, throttle: Throttle) -> list[dict]:
    """Execute a SPARQL query. Wikidata requires a descriptive UA and returns JSON."""
    throttle.wait()
    r = session.get(
        SPARQL_ENDPOINT,
        params={"query": sparql, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("results", {}).get("bindings", [])


def binding_to_record(binding: dict, investor_type: str) -> InvestorRecord | None:
    """Map a single SPARQL binding row to InvestorRecord."""
    name = binding.get("firmLabel", {}).get("value")
    if not name or name.startswith("Q"):  # unlabeled entity, skip
        return None

    qid_uri = binding.get("firm", {}).get("value", "")
    qid = qid_uri.rsplit("/", 1)[-1] if qid_uri else ""

    return InvestorRecord(
        investor_name=name,
        investor_type=investor_type,
        firm_name=name,
        website=binding.get("website", {}).get("value"),
        country=binding.get("countryLabel", {}).get("value"),
        geographic_focus=binding.get("countryLabel", {}).get("value"),
        sector_focus=None,  # Wikidata sector is sparse; AI enrichment fills later
        source_url=qid_uri or f"https://www.wikidata.org/wiki/{qid}",
        data_source="Wikidata",
        date_collected=date.today().isoformat(),
        city=binding.get("hqLabel", {}).get("value"),
        confidence_score=0.85,  # Wikidata is community-curated; high but not authoritative
        notes=f"Wikidata QID: {qid}",
    )


def collect(output_path: Path | None = None) -> int:
    if output_path is None:
        output_path = Path(__file__).resolve().parents[1] / "data/raw/wikidata.jsonl"

    session = make_session()
    throttle = Throttle(delay=2.0)  # Wikidata asks for slow polite queries

    all_records: list[dict] = []
    for investor_type, sparql in QUERIES.items():
        log.info("Running Wikidata query for %s", investor_type)
        try:
            bindings = run_query(session, sparql, throttle)
        except Exception as e:  # noqa: BLE001
            log.error("  query failed for %s: %s", investor_type, e)
            continue

        type_records = 0
        for b in bindings:
            rec = binding_to_record(b, investor_type)
            if rec:
                all_records.append(rec.to_dict())
                type_records += 1
        log.info("  -> %d records for %s", type_records, investor_type)

    write_jsonl(output_path, all_records)
    log.info("Wikidata collection complete: %d records", len(all_records))
    return len(all_records)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wikidata investor collector")
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
