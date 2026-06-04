# SDA — ESG Quantitative Model: Data Ingestion & Preprocessing

Full reference implementation of **`SDA-Data-Ingestion-Preprocessing-v3`**
(Phase 1 + Phase 2) for an ESG quantitative-modelling system covering ~310
listed Vietnamese tickers. Every numbered section of the SDAD is implemented;
section references (e.g. *§3.2*) appear in each module's docstring.

- **Phase 1 — Data Ingestion**: Celery-scheduled pull of Fama-French 6-factor
  inputs from `vnstock` (retry + CSV fallback), a 3-tier crawler (HOSE/HNX,
  Google CSE, RSS) behind a Redis rate-limit/quota guard, malware-scanned &
  SHA-256-deduplicated downloads, and Local + S3 (versioned) storage with a
  PostgreSQL-backed document state machine.
- **Phase 2 — Preprocessing**: text/table extraction (PyMuPDF, Camelot,
  pdfplumber, Tesseract OCR), cleaning & semantic chunking (≤ 200 tokens for
  PhoBERT), ESG taxonomy tagging (Aho-Corasick), JSONL output, and §11 SLA
  metrics.

## Layout

```
sda_esg_pipeline/
├── pyproject.toml / requirements.txt / .env.example / .gitignore
├── Dockerfile / docker-compose.yml          # sandboxed worker + local stack (§4.1, §1)
├── docker/apparmor-pdf-extractor.profile    # AppArmor confinement (§4.1)
├── config/esg_taxonomy.json                 # SME-maintained ESG dictionary (§9)
├── esg_pipeline/
│   ├── cli.py                               # `esg-pipeline` console script
│   ├── common/
│   │   ├── config.py        # typed Settings from env (§2.1)
│   │   ├── secrets.py       # AWS Secrets Manager / Vault / env (§4.1)
│   │   ├── exceptions.py    # PipelineError hierarchy (§5)
│   │   ├── state.py         # DocumentState machine + transition rules (§4.2)
│   │   ├── monitoring.py    # Slack alerting on error rate (§5)
│   │   ├── security.py      # secure_filename / safe_join (§4.1)
│   │   └── logging_config.py
│   ├── phase1_ingestion/
│   │   ├── quantitative.py  # vnstock → FF6 inputs + retry/CSV fallback (§2)
│   │   ├── crawler_tier1.py # HOSE/HNX DOM scraping (§3.1)
│   │   ├── crawler_tier2.py # Google CSE + circuit breaker + cache (§3.2)
│   │   ├── crawler_tier3.py # RSS aggregator (§3.3)
│   │   ├── deduplication.py # NFC + SHA-256 news dedup (§3.4)
│   │   ├── rate_limiter.py  # Redis rate limit + daily quota (§1, §3.2)
│   │   ├── downloader.py    # download + malware scan + SHA-256 (§1)
│   │   ├── storage.py       # Local + S3 (Object Versioning) (§1)
│   │   └── metadata_repo.py # PostgreSQL/SQLite state persistence (§1, §4.2)
│   ├── phase2_preprocessing/
│   │   ├── extraction.py    # PyMuPDF/Camelot/pdfplumber/Tesseract (§7)
│   │   ├── cleaning.py      # clean + semantic chunk ≤200 tokens (§8)
│   │   ├── taxonomy.py      # Aho-Corasick weighted tagging (§9)
│   │   ├── orchestrator.py  # end-to-end → JSONL (§10)
│   │   └── metrics.py       # CER / table / boundary / FP / throughput SLAs (§11)
│   └── scheduler/
│       ├── celery_app.py    # Celery app + Beat schedule (§1)
│       └── tasks.py         # scan_quantitative / crawl_reports / poll_rss / process_document
├── scripts/run_demo.py                      # §10 I/O demo (runs offline)
└── tests/                                   # 19 offline tests (no network/DB/Redis)
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # full stack

# End-to-end Phase 2 demo — works even with NO heavy deps installed:
python scripts/run_demo.py
#   or, after `pip install -e .`:
esg-pipeline demo

# REAL data (no API key): pull FF6 quant inputs via the FREE vnstock package:
python scripts/fetch_quant_demo.py --tickers VNM,FPT,HPG

# REAL data: run Phase 2 on a real report PDF (or a generated sample) → JSONL:
python scripts/run_phase2_pdf.py --ticker DEMO --year 2023            # offline sample
python scripts/run_phase2_pdf.py --url <report.pdf> --ticker VNM --year 2024

# Run the test suite (offline, uses built-in fallbacks):
pytest -q          # 21 passed
```

> 🇻🇳 Hướng dẫn chạy & đánh giá SDA bằng tiếng Việt:
> [docs/HUONG_DAN_CHAY.md](docs/HUONG_DAN_CHAY.md) · [docs/DANH_GIA_SDA.md](docs/DANH_GIA_SDA.md)

### Graceful degradation

The pipeline imports and runs the Phase 2 text path with a minimal install;
heavy deps are imported lazily and fall back where possible:

| Capability                     | Library                               | If missing |
|--------------------------------|---------------------------------------|------------|
| Exact token counting           | `transformers` + `vinai/phobert-base` | whitespace word count |
| O(n) taxonomy matching         | `pyahocorasick`                       | pure-Python scanner (same results) |
| PDF text / OCR / tables        | `PyMuPDF`/`pytesseract`/`camelot`/`pdfplumber` | needed only for real PDFs |
| Quant / crawler / infra        | `vnstock`/`redis`/`googleapiclient`/`celery`/`boto3`/`psycopg2` | needed only for live ingestion |

`scripts/run_demo.py` and all of `tests/` need **none** of the heavy deps.

## Running the full pipeline (§1)

```bash
# 1. Local infra (Redis + Postgres) + worker + beat:
docker compose up --build

# 2. Or run components directly:
celery -A esg_pipeline.scheduler.celery_app:app worker -Q phase2,phase1 --loglevel=INFO
celery -A esg_pipeline.scheduler.celery_app:app beat   --loglevel=INFO
```

Beat schedule: `scan_quantitative` (02:00 daily), `crawl_reports` (03:00
daily), `poll_rss` (every 30 min). A 429 quota error trips the circuit breaker
and routes the payload to the `dlq` queue (§3.2).

## Phase 2 usage

```python
from esg_pipeline.phase2_preprocessing.orchestrator import process_single_document

# metadata.json: {absolute_storage_path, ticker, fiscal_year, pdf_type_flag}
n = process_single_document("doc_meta.json", output_dir="./out")   # → ./out/VNM_2023_chunks.jsonl
```

## Security (§4.1)

- **Secrets**: `common.secrets.get_secret()` resolves from AWS Secrets Manager
  / HashiCorp Vault (`SECRET_BACKEND`), env only for local dev. Never commit `.env`.
- **Directory-traversal**: `common.security.safe_filename` / `safe_join`
  (backed by `werkzeug.utils.secure_filename`).
- **PDF sandboxing**: extraction runs as `nobody` in the container
  ([Dockerfile](Dockerfile)) under the AppArmor profile
  ([docker/apparmor-pdf-extractor.profile](docker/apparmor-pdf-extractor.profile)).
- **Malware**: every download is ClamAV-scanned before persistence (`downloader.py`).

## Quality SLAs (§11)

`esg_pipeline.phase2_preprocessing.metrics` measures and gates each target via
`evaluate_slas()`:

| Metric        | Target            |
|---------------|-------------------|
| OCR CER       | < 5%              |
| Table Ext.    | > 85%             |
| Boundary Err. | < 1%              |
| Taxonomy FP   | < 10%             |
| Throughput    | > 500 pages / sec |

## Notes on fidelity to the spec

A full, prioritised assessment of where the SDAD is unrealistic/inconsistent —
and every patch applied — is in **[docs/DANH_GIA_SDA.md](docs/DANH_GIA_SDA.md)**
(Vietnamese). Highlights:

- **Quant engine uses FREE vnstock**, not the paid `vnstock_data` Sponsor Tier
  the SDAD §2.3 snippet imports (which isn't installable). `quantitative.py`
  targets the real public API and is verified against live data.
- **§10 output is now reproducible** — `cleaning.normalize_ocr_artifacts` repairs
  the leet input (`N3t Zer0`→`Net Zero`, protecting `CO2`/`kWh`), and
  `heading_context` is threaded through metadata, so `run_demo.py` emits exactly
  the documented record.
- **Diacritic-insensitive taxonomy** — the SME dictionary is no-diacritic but
  real reports use full diacritics; matching now folds diacritics (on the real
  VNM 2024 report this lifted E-only → full E/S/G, 12 → 51 chunks).
- **Throughput SLA split** — the ">500 pages/sec" target is the AC+chunking
  micro-benchmark (met, ~600 pps); a realistic end-to-end SLA bounded by
  extraction/OCR was added (`metrics.throughput_e2e_pps`).
- **`clean_text_advanced` step order** — footer-noise removal runs *before* the
  newline-collapse (the SDAD order lets single-newline footers escape the
  `^…$` filter); pages are joined with a blank line so paragraphs survive.
- **Downloader** sends a browser User-Agent (real IR servers 403 the default).
- **Windows UTF-8** — stdout/stderr are reconfigured so the vnstock banner can't
  crash cp1252 consoles.
- **Token counting** uses PhoBERT when available; offline fallback is a
  conservative whitespace count.
- **`metadata_repo`** targets PostgreSQL in Production; the SQLite backend is for
  local dev/tests and shares identical SQL.
```
