# Investor Dataset Pipeline

A modular, multi-source pipeline that builds a structured dataset of
**2,000 investors** (VCs, family offices, angels, private equity firms,
accelerators, and more) from public, lawful data sources — ready for
analytics, CRM ingestion, or AI model use.

Built for the Finance and AI Team Data Engineering Assignment.

## What this is

The pipeline collects investor records from **four independent public
sources**, normalizes them to a unified schema, deduplicates with fuzzy
matching, enriches investment thesis / sector / stage fields using an
LLM (Claude) on text scraped from each firm's own website, validates
the result, and exports a final CSV + JSON.

It runs end-to-end with one command, but every stage is also a
standalone CLI so individual steps can be re-run or tested.

## Sources

| Source                   | Type                              | Why                                                   |
| ------------------------ | --------------------------------- | ----------------------------------------------------- |
| **SEC EDGAR**            | Public US filings API             | Authoritative for US RIAs; no auth, ~15K candidate firms |
| **Wikidata**             | Public SPARQL endpoint            | Global coverage; structured fields for VC/PE/family offices |
| **OpenVC**               | Public VC directory               | Already curated; explicitly free-to-use list          |
| **YC + accelerator team pages** | Public team / partner pages | Adds angels + accelerator firm records                |

Each record is tagged with its `data_source`, `source_url`, and
`date_collected`. When the same firm appears in multiple sources, dedup
merges them and concatenates the provenance.

## Design principles

1. **AI structures data, it does not invent data.** The LLM step takes
   text scraped from a firm's own website and produces structured fields
   (thesis, sector, stage). The prompt forces null returns when the
   source text doesn't support a field, and stores a verbatim evidence
   phrase. We never ask the LLM "what does Sequoia Capital invest in"
   from prior knowledge.

2. **Provenance over volume.** Every record can be traced back to a
   specific URL. The dataset prefers 2,000 verifiable records over 5,000
   uncertain ones.

3. **Polite scraping.** Honest User-Agent, robots.txt honored, throttling
   per host, no CAPTCHA or paywall circumvention. Public structured APIs
   (SEC EDGAR, Wikidata SPARQL) are preferred over HTML scraping where
   available.

4. **Modular and re-runnable.** Each collector outputs to JSONL in
   `data/raw/`; the normalize/dedupe/validate/export stages are
   independent. Easy to swap or extend a single source without
   touching the rest.

## Repository layout

```
investor-pipeline/
├── collectors/              # one module per data source
│   ├── schema.py            # InvestorRecord dataclass + controlled vocab
│   ├── utils.py             # polite HTTP, throttling, JSONL I/O
│   ├── sec_edgar.py
│   ├── wikidata.py
│   ├── openvc.py
│   └── accelerators.py
├── enrichment/              # post-collection enrichment
│   ├── website_enricher.py  # fetches firm "About" pages
│   └── ai_enricher.py       # Claude API: structures the scraped text
├── validation/              # cleaning + QA
│   ├── normalize.py         # unified schema, controlled vocab, URL/country
│   ├── dedupe.py            # fuzzy name + domain match
│   ├── validate.py          # URL liveness, email regex, confidence
│   └── export.py            # final CSV + JSON
├── data/
│   ├── raw/                 # per-collector JSONL (gitignored)
│   └── processed/           # intermediate (gitignored)
├── output/                  # final deliverables
│   ├── Investor_Dataset.csv
│   └── Investor_Dataset.json
├── docs/
│   ├── Methodology.md
│   └── Final_Summary.md
├── run_pipeline.py          # one-command end-to-end runner
├── requirements.txt
└── README.md
```

## Setup

```bash
git clone <this-repo>
cd investor-pipeline
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: for the AI enrichment step, set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

End-to-end:

```bash
python run_pipeline.py
```

Skip AI enrichment (no API cost, but `investment_thesis` / refined
sector fields will be sparser):

```bash
python run_pipeline.py --skip-ai
```

Smoke test with tiny limits:

```bash
python run_pipeline.py --quick
```

Run a single stage:

```bash
python -m collectors.sec_edgar --limit 1500
python -m validation.normalize
python -m validation.dedupe
python -m validation.export
```

## Output

`output/Investor_Dataset.csv` and `output/Investor_Dataset.json` —
2,000 records max, sorted by `confidence_score` descending.

Each record has the 10 mandatory fields from the spec, plus nice-to-have
fields where the source supported them. Records that don't meet a
minimum confidence threshold are dropped at export time.

A companion `output/Investor_Dataset.stats.json` summarizes the
breakdown by source, type, and country.

## Schema

See `collectors/schema.py` for the full dataclass.

**Mandatory** (every record): `investor_name`, `investor_type`,
`firm_name`, `website`, `country`, `geographic_focus`, `sector_focus`,
`source_url`, `data_source`, `date_collected`.

**Nice-to-have** (when available): `city`, `investment_thesis`,
`investment_stage`, `typical_ticket_size`, `portfolio_companies`,
`key_people`, `contact_email`, `contact_phone`, `linkedin_url`,
`confidence_score`, `notes`.

## Scaling beyond 2,000

The pipeline is designed to scale 50× to 100× without architectural
change:

- SEC EDGAR alone covers ~15K+ candidate firms; the `--sec-limit` flag
  uncaps that.
- Each Wikidata SPARQL query can be sharded by country (P17 filter) to
  parallelize beyond the default LIMIT 1500.
- The collector–normalize–dedupe split means adding a 5th, 10th, 20th
  source is one new module plus a line in `run_pipeline.py`.
- For 100K+ records, swap JSONL/CSV for a real store (Postgres + SQLAlchemy,
  or DuckDB for analytics workloads); swap the in-memory dedupe for a
  blocking-key index in Redis.

See `docs/Final_Summary.md` for the full scaling discussion.

## License

Code is the property of the Company per the assignment IP terms.
No public data was unlawfully obtained; all source data carries its
original license.
