# Methodology

## Investor Dataset Pipeline — Sources, Tools, and Process

**Author:** Ameya Bhalerao
**Submission:** Data Engineering Assignment — Finance and AI Team

---

## 1. Sources used and why

The dataset draws from four independent public sources, chosen for
distinct strengths:

### SEC EDGAR (US registered investment advisers)

The SEC's EDGAR system exposes structured filings for every US-registered
investment adviser via a free, no-auth JSON API. The `company_tickers.json`
endpoint lists ~10,000 entities; the `submissions/CIK*.json` endpoint
returns each entity's metadata (legal name, business address, SIC code,
website). Filtering on investor-relevant SIC codes (6282 Investment
Advice, 6770 Holding & Investment Offices, 6726, 6199) yields a clean
seed of authoritative US investor records.

**Why selected:** Authoritative (SEC filings, not marketing copy), no
scraping required, no auth, generous published rate limit (10 req/s).
This is the highest-confidence source in the dataset.

### Wikidata (global structured knowledge)

Wikidata exposes a free SPARQL endpoint with a community-curated knowledge
graph. Four targeted queries (one each for venture capital firms, private
equity firms, family offices, and accelerators) pull firms globally with
country, headquarters city, official website, and inception date when
present.

**Why selected:** Genuinely global (fills the non-US gap left by EDGAR),
already structured, no scraping politics. Quality is community-curated —
lower than SEC filings but still verifiable via each entity's Wikidata
page.

### OpenVC

OpenVC publishes a public investor directory marketed as a free,
exportable resource. Their HTML listing exposes investor name, type,
country, sector focus, stage focus, and typical check size for
thousands of VCs and angels.

**Why selected:** Already-curated, explicitly designed for public use,
covers many VCs absent from SEC EDGAR (international firms, smaller
funds below the $100M registration threshold).

### Accelerators / Y Combinator partner pages

YC, Techstars, and 500 Global publish public team / partner pages.
These contribute (a) a record for each accelerator firm itself and
(b) records for individually-named partners who often invest as angels.

**Why selected:** Captures the "angel" investor type that no structured
source covers well, plus authoritative records for the accelerators
themselves.

### Sources considered but rejected

- **Crunchbase, PitchBook, Preqin** — paywalled; scraping violates ToS.
- **LinkedIn** — login-walled; scraping violates ToS and is legally
  contested (HiQ v. LinkedIn). Public LinkedIn URLs are *included* in
  records when they appear in another source, but no LinkedIn page is
  scraped directly.
- **AngelList / Wellfound** — significant content sits behind login.
- **Firm-specific scraping at scale** — fragile, slow, ethically
  borderline at volume. Used only as a targeted *enrichment* step
  after collection (Stage 4 below), with robots.txt honored.

---

## 2. Tools and libraries

| Category              | Tool                                       |
| --------------------- | ------------------------------------------ |
| Language              | Python 3.10+                               |
| HTTP / scraping       | `requests`, `urllib3` retries, `BeautifulSoup4` |
| Data structures       | `dataclasses`, `pandas` (export only)      |
| Fuzzy matching        | `rapidfuzz`                                |
| Country normalization | `pycountry`                                |
| AI enrichment         | `anthropic` (Claude API)                   |
| Concurrency           | `concurrent.futures.ThreadPoolExecutor`    |
| Versioning            | `git` + private GitHub repository          |

No browser automation (Selenium / Playwright) was needed — every source
either exposes a structured API or serves listings as plain HTML.

---

## 3. How AI tools were used

This is the part of the assignment where the line between "good use" and
"bad use" matters most, so I want to be explicit.

### What AI does in this pipeline

The AI enrichment step (`enrichment/ai_enricher.py`) takes a firm's name
and a chunk of text *that has already been scraped from that firm's own
public website*, and returns structured fields:

- `investment_thesis` (one-sentence summary)
- `sector_focus` (comma-separated list, refined from raw text)
- `investment_stage` (controlled vocab: pre-seed, seed, Series A, …)
- `geographic_focus` (regions explicitly mentioned)
- `confidence` (0.0–1.0, the model's own confidence)
- `evidence_phrase` (verbatim quote <15 words from source text)

The prompt explicitly tells the model: do not use prior knowledge, return
null when the text doesn't support a field, return all-null with
confidence 0.0 if the text is navigation junk rather than real content.

### What AI does *not* do

- AI is never asked to **generate investor names** from prior knowledge.
- AI is never asked to **fill country / website / contact** fields — those
  come from the source feeds only.
- AI never **invents** a thesis. If the firm's website says nothing about
  thesis, the field stays null.

This separation — AI as a structuring layer over verifiable raw text,
not as a fact generator — is what makes the AI usage defensible for
downstream finance / fundraising use.

### Choice of model

Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) was used for cost-efficiency.
At ~3000 characters of input text and a small JSON output, per-record
cost is well under a cent. For 2,000 records the total API cost is
roughly $10–20.

### Other LLM uses in development

I used Claude in conversation for code review, schema design discussion,
and copy editing of these docs. None of that AI-assisted code generates
investor data at runtime.

---

## 4. How duplicates were removed

The dedupe stage (`validation/dedupe.py`) runs after schema normalization
so all records use the same country names, URL formats, and investor
types.

**Blocking + fuzzy match approach:**

1. **Phase 1 — exact domain match.** Records with the same normalized
   website domain are unioned into a cluster. This is the highest-confidence
   dedupe signal (a firm has one canonical website).

2. **Phase 2 — fuzzy name match.** Names are normalized (lowercase, strip
   "Inc / LLC / Ltd / Capital / Ventures" suffixes, strip punctuation),
   then `rapidfuzz.token_sort_ratio` compares pairs within first-3-letter
   blocks. Pairs scoring ≥88 are unioned.

3. **Phase 3 — union-find cluster collapse.** Within each cluster, the
   record with the highest confidence_score wins (ties broken by source
   priority: SEC EDGAR > Wikidata = OpenVC > YC). Other cluster members'
   non-null fields fill any gaps in the survivor. Source attribution is
   concatenated so provenance isn't lost.

The result: a firm that appears in three sources becomes one record with
the strongest fields from each, and a `data_source` field listing all
three.

---

## 5. How data quality was checked

Three quality layers, each producing artifacts:

1. **Schema validation.** Every record passes through `normalize.py`,
   which enforces the controlled vocabulary for `investor_type`, ISO
   country names (via `pycountry`), canonical URL form, and required
   fields. Records missing investor_name OR source_url are dropped at
   this stage (impossible to verify or cite).

2. **URL liveness.** The validation stage performs a HEAD request on each
   unique website domain (cached, so 1 request per domain regardless of
   record count). Dead domains get flagged in `notes` but the record is
   kept — many real firms have stale homepages but verifiable
   identities via SEC filings.

3. **Confidence scoring.** Each record gets a `confidence_score` in
   `[0, 1]`, blended from:
   - Source authority (SEC: 0.95, Wikidata: 0.85, OpenVC: 0.80, etc.)
   - Field completeness (fraction of mandatory + optional fields filled)
   - AI confidence (if AI enrichment ran)

   The final export drops records below `min_conf=0.3` and sorts the
   rest by confidence descending, so reviewers see the strongest
   records first.

---

## 6. Limitations and honest gaps

This dataset has real limits and I'd rather state them up front than
hide them:

1. **Coverage skew toward US firms.** SEC EDGAR is US-only, and OpenVC
   skews toward English-language listings. Wikidata partially fixes this
   but still favors firms with English Wikipedia entries. A future
   expansion would add: regional VC databases (e.g., Latitude, Dealroom
   for Europe; AVCJ for Asia), and country-specific business registries.

2. **Family offices are under-represented.** Family offices generally
   stay private. The Wikidata family-office query yields only ~50
   records globally. Realistically expanding this requires partnerships
   with private directories, which is out of scope for this assignment.

3. **Contact fields are sparse.** I deliberately did not scrape email
   addresses from third-party aggregator sites or use email-finding
   tools that pattern-guess addresses — both raise consent / legal
   concerns. The `contact_email` field only fills when an email is
   explicitly listed on a firm's own public page.

4. **HTML scraping is fragile.** The OpenVC and accelerator collectors
   depend on current HTML structure. If OpenVC redesigns their listings
   page, that collector will yield zero records and the pipeline will
   continue with the other three sources — but the dataset will be
   smaller. This is honest about the trade-off; the structured-API
   sources (EDGAR, Wikidata) are immune to this.

5. **No real-time freshness signal.** `date_collected` is when this
   pipeline ran; we don't know when the underlying source last updated
   each record. A production version would track per-source update
   cadence and prioritize re-pulling stale ones.

6. **AI enrichment depends on website quality.** Firms with thin or
   marketing-heavy About pages produce weaker AI-extracted thesis fields.
   The `evidence_phrase` field is included precisely so reviewers can
   audit whether the extracted thesis is grounded in real source text.

---

## 7. Reproducibility

The full pipeline can be re-run from scratch with:

```bash
python run_pipeline.py
```

Each stage writes its output to a versioned file so partial reruns are
possible (`run_pipeline.py --skip-collect` reuses cached raw data).
Random sampling is not used anywhere — given the same source data and
the same dedup threshold, the output is deterministic.

---

## 8. Compliance

All data was collected from public sources with no login, paywall, or
CAPTCHA circumvention. The pipeline:

- Identifies itself with an honest User-Agent including a contact email.
- Honors `robots.txt` on every firm-website request.
- Throttles per host (≥1 request per second for HTML sources, faster
  only for SEC EDGAR which explicitly allows up to 10 req/s).
- Respects HTTP 429 Retry-After headers.
- Does not store login-required content.

Every record's `source_url` points to the exact public page from which
it was collected, so any field in the dataset is independently verifiable.
