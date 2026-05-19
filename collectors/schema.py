"""
Unified investor record schema.

Every collector normalizes to this shape before writing to data/raw/.
Keeping a single schema here means the deduper, normalizer, and validator
all speak the same language.

Mandatory fields come straight from the assignment spec. Nice-to-have
fields are filled when the source supports them; otherwise left as None
and we don't lie about it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional


# Mandatory per the assignment
MANDATORY_FIELDS = [
    "investor_name",
    "investor_type",
    "firm_name",
    "website",
    "country",
    "geographic_focus",
    "sector_focus",
    "source_url",
    "data_source",
    "date_collected",
]

# Nice-to-have per the assignment
OPTIONAL_FIELDS = [
    "city",
    "investment_thesis",
    "investment_stage",
    "typical_ticket_size",
    "portfolio_companies",
    "key_people",
    "contact_email",
    "contact_phone",
    "linkedin_url",
    "confidence_score",
    "notes",
]

ALL_FIELDS = MANDATORY_FIELDS + OPTIONAL_FIELDS


# Controlled vocabulary for investor_type. Anything else gets normalized
# to "other" by the normalizer so we don't end up with 40 spellings of "VC".
INVESTOR_TYPES = {
    "venture_capital",
    "family_office",
    "angel",
    "private_equity",
    "limited_partner",
    "accelerator",
    "incubator",
    "corporate_venture",
    "strategic_investor",
    "startup_fund",
    "other",
}


@dataclass
class InvestorRecord:
    """One investor. Every collector should emit these."""

    # --- mandatory ---
    investor_name: str
    investor_type: str
    firm_name: Optional[str]
    website: Optional[str]
    country: Optional[str]
    geographic_focus: Optional[str]
    sector_focus: Optional[str]
    source_url: str
    data_source: str
    date_collected: str  # ISO format YYYY-MM-DD

    # --- optional ---
    city: Optional[str] = None
    investment_thesis: Optional[str] = None
    investment_stage: Optional[str] = None
    typical_ticket_size: Optional[str] = None
    portfolio_companies: Optional[str] = None  # comma-separated, easier for CSV
    key_people: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    confidence_score: Optional[float] = None  # 0.0 - 1.0
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def today(cls) -> str:
        return date.today().isoformat()
