"""
Smoke tests — run without network.

These exercise the pure-logic modules (schema, normalize, dedupe) using
synthetic input. They protect against accidentally breaking the
deterministic parts of the pipeline.

Run:
    python -m pytest tests/  -v
    or just:
    python tests/test_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors.schema import InvestorRecord, MANDATORY_FIELDS, INVESTOR_TYPES
from validation.normalize import (
    normalize_country, normalize_url, normalize_investor_type, normalize_record,
)
from validation.dedupe import dedupe, normalize_name_for_match


def test_schema_record_creation():
    r = InvestorRecord(
        investor_name="Sequoia Capital",
        investor_type="venture_capital",
        firm_name="Sequoia Capital",
        website="https://sequoiacap.com",
        country="United States",
        geographic_focus="Global",
        sector_focus="Technology",
        source_url="https://example.com/sequoia",
        data_source="Test",
        date_collected="2026-05-16",
    )
    d = r.to_dict()
    for k in MANDATORY_FIELDS:
        assert k in d, f"missing mandatory field {k}"
    print("PASS: schema_record_creation")


def test_normalize_country():
    assert normalize_country("USA") == "United States"
    assert normalize_country("UK") == "United Kingdom"
    assert normalize_country("Germany") == "Germany"  # pycountry passthrough
    assert normalize_country(None) is None
    assert normalize_country("") is None
    print("PASS: normalize_country")


def test_normalize_url():
    assert normalize_url("https://www.example.com/") == "https://example.com"
    assert normalize_url("example.com").startswith("https://example.com")
    assert normalize_url("https://EXAMPLE.com/path/?q=1") == "https://example.com/path"
    assert normalize_url(None) is None
    print("PASS: normalize_url")


def test_normalize_investor_type():
    assert normalize_investor_type("VC") == "venture_capital"
    assert normalize_investor_type("Venture Capital") == "venture_capital"
    assert normalize_investor_type("private equity") == "private_equity"
    assert normalize_investor_type("Family Office") == "family_office"
    assert normalize_investor_type("Unknown gibberish") == "other"
    assert normalize_investor_type(None) == "other"
    print("PASS: normalize_investor_type")


def test_normalize_record_fills_firm_name_fallback():
    raw = {
        "investor_name": "Andreessen Horowitz",
        "investor_type": "VC",
        "firm_name": None,
        "source_url": "https://a16z.com",
        "data_source": "Test",
        "date_collected": "2026-05-16",
    }
    out = normalize_record(raw)
    assert out["firm_name"] == "Andreessen Horowitz"
    assert out["investor_type"] == "venture_capital"
    print("PASS: normalize_record_fills_firm_name_fallback")


def test_dedupe_merges_same_domain():
    records = [
        {
            "investor_name": "Sequoia Capital",
            "investor_type": "venture_capital",
            "website": "https://sequoiacap.com",
            "source_url": "https://sec.gov/sequoia",
            "data_source": "SEC EDGAR",
            "confidence_score": 0.95,
            "country": "United States",
            "date_collected": "2026-05-16",
        },
        {
            "investor_name": "Sequoia Capital LLC",
            "investor_type": "venture_capital",
            "website": "https://sequoiacap.com",
            "source_url": "https://wikidata.org/Q123",
            "data_source": "Wikidata",
            "confidence_score": 0.85,
            "country": "United States",
            "date_collected": "2026-05-16",
            "investment_thesis": "Backs ambitious founders.",
        },
        {
            "investor_name": "Andreessen Horowitz",
            "investor_type": "venture_capital",
            "website": "https://a16z.com",
            "source_url": "https://example.com/a16z",
            "data_source": "OpenVC",
            "confidence_score": 0.80,
            "country": "United States",
            "date_collected": "2026-05-16",
        },
    ]
    deduped, dropped = dedupe(records)
    assert len(deduped) == 2, f"expected 2 records after dedup, got {len(deduped)}"
    assert dropped == 1
    sequoia = [r for r in deduped if "sequoia" in r["investor_name"].lower()][0]
    # SEC EDGAR record should win (higher confidence), thesis should be merged in
    assert sequoia["data_source"].startswith("SEC EDGAR")
    assert "Wikidata" in sequoia["data_source"]
    assert sequoia["investment_thesis"] == "Backs ambitious founders."
    print("PASS: dedupe_merges_same_domain")


def test_normalize_name_for_match():
    a = normalize_name_for_match("Sequoia Capital LLC")
    b = normalize_name_for_match("Sequoia Capital")
    c = normalize_name_for_match("SEQUOIA CAPITAL, INC.")
    assert a == b == c, f"expected all equal, got {a!r}, {b!r}, {c!r}"
    print("PASS: normalize_name_for_match")


def main():
    test_schema_record_creation()
    test_normalize_country()
    test_normalize_url()
    test_normalize_investor_type()
    test_normalize_record_fills_firm_name_fallback()
    test_dedupe_merges_same_domain()
    test_normalize_name_for_match()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
