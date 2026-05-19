# Final Summary

## Investor Dataset Pipeline

**Author:** Ameya Bhalerao
**Submission:** Data Engineering Assignment — Finance and AI Team

---

## What I built

A modular Python pipeline that produces a clean, structured dataset of
2,000 investors (VCs, PE firms, family offices, angels, accelerators,
and corporate VCs) from four independent public sources, with full
provenance for every field.

The system is **not a one-off scraper script**. Every stage is a
standalone module with its own CLI, the schema is enforced by a
dataclass, dedupe runs with union-find on fuzzy + domain match, and
AI enrichment is a clearly bounded structuring layer over text that
was already publicly available on each firm's own website. The whole
thing runs end-to-end with `python run_pipeline.py`.

The dataset itself ships as both CSV and JSON, sorted by a per-record
`confidence_score` so reviewers see the strongest records first. A
companion `stats.json` breaks down the dataset by source, investor type,
and country.

---

## How the pipeline works

The pipeline runs in seven stages, each one a separate module:

**1. Collection (parallel, one module per source).** SEC EDGAR via the
public submissions API, Wikidata via SPARQL, OpenVC via HTML scraping,
and YC / Techstars / 500 Global via team-page scraping. Each collector
emits JSONL to `data/raw/` so individual sources can be re-run without
disturbing the others.

**2. Normalization.** All raw JSONL files merge into one stream, get
schema-validated, controlled-vocabulary mapped (investor_type),
country-name normalized (pycountry), URL-canonicalized, and
whitespace-cleaned.

**3. Deduplication.** Union-find clusters records by exact domain match
*and* fuzzy name match (rapidfuzz token_sort_ratio ≥ 88) within
first-letter blocks. Cluster survivors keep the highest-confidence
record and absorb non-null fields from siblings; provenance is
concatenated.

**4. Website enrichment.** For each unique firm domain, fetch the
homepage + one About-style page, extract readable text, respect
robots.txt, and stash up to 3000 characters per firm in a side-file.

**5. AI enrichment.** Feed each firm's name + scraped text to Claude
Haiku with a strict no-hallucination prompt. The model returns JSON
with thesis/sector/stage/geography fields, an evidence phrase quoted
from the text, and its own confidence. Fields fill in *only* where the
source record was empty.

**6. Validation.** URL liveness (HEAD requests, cached per domain),
email regex, mandatory-field presence checks, and a final blended
confidence score combining source authority, completeness, and AI
confidence.

**7. Export.** Quality filter (`confidence ≥ 0.3`), sort by confidence
descending, cap at 2000, write CSV + JSON + stats.

---

## How this scales from 2,000 to 100,000+

The architecture is built so growth is mechanical, not a rewrite:

**1. Source axis (more sources).** Each collector is one Python file
implementing the `InvestorRecord` schema. Adding Crunchbase via their
API (paid tier), Dealroom, AVCJ for Asia, government business registries
in target countries, or domain-specific directories (medtech VCs, climate
funds) is a single new module plus one line in `run_pipeline.py`. No
changes to dedupe / normalize / validate / export.

**2. Volume axis (more records per source).** Both structured sources
scale today:
- SEC EDGAR has ~15,000 candidate firms; the `--sec-limit` flag uncaps
  it. At 6 req/s (under the SEC's 10 req/s ceiling), a full pull takes
  ~45 minutes.
- Wikidata SPARQL queries can be sharded by country (`P17` filter) so
  the LIMIT 1500 ceiling per query becomes LIMIT 1500 per country —
  easily 30K+ records.

**3. Infrastructure axis (production-grade store).** At 100K+ records
the JSONL + in-memory dedupe pattern stops working. The replacements
are well-trodden:
- **Storage:** Postgres for the record store, with a `(name_normalized,
  domain)` composite index for blocking. DuckDB for analytics queries
  over the same data.
- **Dedup:** Blocking-key index in Redis (or Postgres GIN trigram), so
  fuzzy comparison stays O(cluster size) rather than O(n²).
- **Pipeline orchestration:** Move from `run_pipeline.py` to Airflow /
  Prefect, one DAG per source, per-source freshness SLAs.
- **AI cost control:** Batch the Anthropic API calls (the Messages
  Batches API), cache by domain so we don't re-enrich firms whose
  websites haven't changed (track content hash), and route obvious
  short/empty pages around the LLM entirely.

**4. Freshness axis (keep it up to date).** Today's pipeline runs once
and stamps `date_collected`. The production version watches each
source's modification cadence — daily for EDGAR, weekly for Wikidata
dumps, on-demand for OpenVC — and writes a `last_verified` timestamp
per field, so downstream consumers can decide what counts as fresh.

The point is that none of this changes the public-API of the schema or
the way a downstream consumer queries the data. The interface stays
stable while the volume and update rate grow underneath.

---

## What I would improve with more time

In rough priority order:

1. **More non-US sources.** EDGAR's US-centricity is the single biggest
   coverage gap. With another week I'd build collectors for the UK FCA
   register, Crunchbase free tier, and at least one Asian regional VC
   directory. Goal: 40% of records non-US.

2. **Per-source unit tests.** Right now the test directory is empty
   scaffolding. The collectors and the dedup module both deserve real
   pytest coverage — fixture HTML for the scrapers, known dupe pairs
   for the deduper.

3. **Schema versioning.** The `InvestorRecord` dataclass would gain a
   version field, with a small migration script when the schema evolves.
   Important for a dataset that gets re-pulled monthly.

4. **Embedding-based dedup as a second pass.** Fuzzy string match misses
   "Sequoia Capital" vs "Sequoia Capital India" (genuinely different
   firms) and the reverse case where the same firm has very different
   names in two languages. A small sentence-transformer over
   `name + country + website` could catch these.

5. **A web dashboard.** Streamlit page that loads the final dataset
   plus stats.json, lets a reviewer filter by source / type / country,
   and shows the evidence_phrase tooltip for AI-enriched fields. This
   makes the QA story interactive instead of CSV-only.

6. **Differential pulls.** Today the pipeline always does a full pull.
   A production version would diff against the last run and only
   re-fetch / re-enrich changed records, cutting cost and API load
   dramatically.

---

## How this dataset is ready for AI model ingestion

The structure was designed with downstream AI use in mind from the start:

**Clean schema, consistent types.** Every field has a defined type and
controlled vocabulary where applicable. `investor_type` is one of nine
known values, not free text — embeddings and classifiers won't fight
spelling variants. `country` is ISO short names. Stages and sectors
are comma-separated atomic terms.

**Provenance per record.** Every record carries `source_url`,
`data_source`, and `date_collected`. An RAG system retrieving these
records can cite where it knows what it knows from — directly addressing
the hallucination problem in finance-domain LLM applications.

**Confidence-aware ranking.** `confidence_score` lets downstream
ranking, retrieval, or training pipelines weight records appropriately.
Authoritative SEC records can outrank crowd-sourced Wikidata entries
when the application requires high precision.

**AI-friendly text fields.** `investment_thesis` and `notes` are
single-paragraph natural-language summaries — exactly the shape that
embedding models want. The `evidence_phrase` field stores the
extracted quote that supports each AI-enriched fact, so a downstream
QA system can show its work.

**Easy join keys.** Normalized website domain + normalized firm name
together form a near-unique identifier, ready for joining against
deal-flow data, CRM exports, or LinkedIn company URLs without further
normalization.

**Format flexibility.** CSV for spreadsheet review and Excel ingestion,
JSON for direct programmatic use. The same data, byte-equal modulo
serialization order.

Whether the downstream use is a fundraising CRM, a fund-matching
RAG agent, or training data for an investor-classification model,
this dataset is ready to plug in.

---

## Summary

The pipeline is built around a simple discipline: every fact in the
final dataset is either (a) directly copied from a verifiable public
source with the source URL recorded, or (b) extracted by an LLM from
text that meets the same standard, with the source URL *and* the
evidence phrase recorded. That discipline is what makes the dataset
trustworthy for downstream finance and AI workflows, and it's what
makes the pipeline scale honestly to 100K+ records without quietly
fabricating its own ground truth.

I appreciated the assignment — it's the right kind of problem to test
data engineering thinking, because the easy paths (manual collection,
scraping login-walled sites, asking the LLM to "list 2,000 VCs") all
fail in ways that would matter at production scale, and the harder
path forces structural decisions that pay off as the system grows.
