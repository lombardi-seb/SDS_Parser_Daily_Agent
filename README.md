# SDS Parser 🧪
### Automated Safety Data Sheet Analysis & GHS Data Extraction

---

## Overview

**SDS Parser** is a Python tool for automated extraction and classification of chemical hazard data from Safety Data Sheet (SDS) documents. Given a material identifier (Z-number), the tool retrieves the corresponding SDS PDF from an Oracle database, extracts GHS-compliant hazard information, and uploads the structured output directly to a **CISPro** (Biovia) chemical inventory system via REST API.

The tool can be used three ways:

- **Interactive mode** — a Gradio web interface (embedded in a Jupyter Notebook) for single or batch analysis.
- **Daily agent** — an unattended, stateful pipeline that discovers each day's new materials, processes them, and writes full run artefacts. Safe to schedule and safe to restart after a crash.
- **Fixed-list mode** — the same agent, fed an explicit `ids.txt` instead of the daily DB discovery (this replaces the former standalone `batch_runner.py`).

---

## Features

- 📄 **PDF text extraction** — PyMuPDF (`fitz`) for fast parsing of digital SDS PDFs.
- 🔍 **OCR fallback** — automatic switch to Tesseract OCR when the text layer is missing or poor quality (scanned PDFs). The choice is driven by a text-quality heuristic (`assess_text_quality`).
- ⚠️ **GHS hazard extraction** — identifies H-codes, GHS pictograms and signal words against a curated reference dictionary (`ghscode_10.txt`) covering the full UN GHS catalogue. H-code suffix casing is preserved, so `H360Fd` (*suspected* of damaging the unborn child) is never conflated with `H360FD` (*may* damage it).
- 🎯 **Section-2 scoping** — H-code search is restricted to **section 2** of the SDS (the authoritative GHS/CLP product classification section) when that section can be located, and falls back to a full-document scan otherwise. This avoids false positives from component tables (section 3), toxicological data (section 11) and glossaries (section 16). Each run also stores a parallel comparison between the section-2 result and the full-document result (see *Section-2 scoping* below).
- 🤖 **LLM-assisted extraction** — a single, JSON-mode call via [OpenRouter](https://openrouter.ai/), validated by Pydantic, extracts physicochemical properties (physical state, boiling point, flash point) and a storage & handling summary.
- 📦 **Dual JSON output**:
  - **Main JSON** — GHS hazard classification (label codes, pictograms, signal word).
  - **Additional JSON** — PPE recommendations, physicochemical data, storage & handling.
- 🔗 **CISPro integration** — uploads both JSONs to CISPro via authenticated REST API.
- 🗂️ **Stateful agent** — idempotency (no double-processing), per-ID crash safety, JSONL trajectory log and JSON run summary.
- 🖥️ **Gradio UI** — interactive analysis without writing any code.

---

## Tech Stack

| Component | Technology |
|---|---|
| PDF text extraction | [PyMuPDF](https://pymupdf.readthedocs.io/) (`fitz`) |
| OCR fallback | [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) + [pytesseract](https://github.com/madmaze/pytesseract) |
| Database | Oracle DB via [python-oracledb](https://python-oracledb.readthedocs.io/) |
| LLM calls | [OpenRouter API](https://openrouter.ai/) (configurable model) |
| Schema validation | [Pydantic](https://docs.pydantic.dev/) v2 |
| Config / secrets | [python-dotenv](https://github.com/theskumar/python-dotenv) |
| Web interface | [Gradio](https://www.gradio.app/) (pinned to `5.37.*`) |
| Chemical inventory | CISPro (Biovia) REST API |
| Data handling | pandas, chardet |
| Language | Python 3.9+ (developed/deployed on 3.12) |

---

## Project Structure

A **single project**, organised by folder. The shared core (`SDS_functions.py`, `config.py`, `llm_extraction.py`, `ghscode_10.txt`) stays at the root because both the UI and the agent import it — keeping one copy avoids divergence.

```
SDS_Parser/
├── SDS_functions.py            # Core logic: DB access, PDF parsing, OCR, H-code extraction, LLM calls, API upload
├── llm_extraction.py           # Single JSON-mode LLM call + Pydantic validation (SDSExtraction)
├── config.py                   # Thin reader: loads .env and exposes the settings to the rest of the code
├── ghscode_10.txt              # Reference TSV: H-codes → GHS categories, pictograms, signal words, P-codes
├── models.txt                  # OpenRouter-compatible LLM model names
├── requirements.txt
├── .env                        # ALL configuration + secrets (gitignored)
│
├── gradio_app/                 # Interactive UI
│   ├── gradio_callbacks.py     # UI callbacks wired to the Gradio interface
│   └── SDS_parser_gradio.ipynb # Notebook — launch this for the interactive UI
│
└── agent/                      # Unattended daily agent (Python package)
    ├── __init__.py
    ├── agent_schema.py         # Pydantic models (run state, per-material result, steps)
    ├── db_discovery.py         # Step 1 — Oracle query → today's Z-numbers
    ├── state_manager.py        # Step 2 — idempotency (agent_state.json)
    ├── reporter.py             # Step 5 — JSONL trace + JSON summary
    └── daily_agent.py          # Entry point (python -m agent.daily_agent)
```

> The `agent/` folder must stay a package (keep `__init__.py`) and `SDS_functions.py` must remain importable at the root, since the agent is launched with `python -m agent.daily_agent` and does `from SDS_functions import ...`.

---

## How It Works

```
Material ID (Z-number)
        │
        ▼
 ┌─────────────────────────┐
 │  Oracle DB              │  Retrieve SDS PDF blob by Z-number → node ID
 └──────────┬──────────────┘
            │
            ▼
 ┌─────────────────────────┐
 │  PDF Parsing            │  PyMuPDF text extraction
 └──────────┬──────────────┘
            │
   Text quality good?
      ├─ NO ───────────────►  ┌──────────────────────────┐
      │                       │  OCR (Tesseract)          │
      │                       └──────────────┬───────────┘
      └─ YES ◄───────────────────────────────┘
            │
            ▼
 ┌─────────────────────────┐
 │  Section-2 scoping      │  Isolate SDS section 2 if found,
 │                         │  else use the full document
 └──────────┬──────────────┘
            │
            ▼
 ┌─────────────────────────┐
 │  H-Code Extraction      │  Match against ghscode_10.txt (casing preserved)
 │                         │  → label codes, pictograms, signal word
 └──────────┬──────────────┘
            │
            ├──────────────────────────────────────────►
            │                               ┌───────────────────────────────┐
            │                               │  LLM call (OpenRouter, opt.)  │
            │                               │  → physical state, boiling pt,│
            │                               │  flash point, storage &       │
            │                               │  handling  (PPE is rule-based)│
            │                               └──────────────┬────────────────┘
            ▼                                              ▼
 ┌─────────────────────┐              ┌────────────────────────────────┐
 │  Main JSON          │              │  Additional JSON                │
 │  (GHS hazard data)  │              │  (PPE + physicochemical data)   │
 └────────┬────────────┘              └────────────────┬───────────────┘
          │                                            │
          └───────────────────┬────────────────────────┘
                              ▼
                    ┌─────────────────────┐
                    │  CISPro REST API    │  POST main JSON + PUT additional JSON
                    └─────────────────────┘
```

---

## Section-2 scoping

Under GHS/CLP, **section 2** of an SDS is the authoritative source for the *product-level* hazard classification. Other sections legitimately contain H-codes that do **not** describe the product itself — for example component classifications in section 3, or hazard statements quoted in the toxicological (section 11) and glossary (section 16) sections. Scanning the whole document therefore risks over-classifying a product (e.g. picking up an `H240` from a component and misclassifying the product as explosive).

To prevent this, the H-code search is **scoped to section 2 when that section can be located**, and falls back to a full-document scan only when it cannot. Section boundaries are detected from the regulatory invariant that GHS/CLP mandates 16 numbered sections in a fixed order — the section *number* is language-invariant, so the same logic works across EN/FR/IT/DE documents.

To quantify the impact of this scoping and guard against regressions, each run also performs a **parallel full-document scan** and stores a comparison alongside the run artefacts (`shadow_section2.jsonl`). This audit trail records, per material, whether the section-2 result and the full-document result agree — without affecting the production output.

> **Note:** an SDS whose section 2 contains *no* H-codes is a valid, non-hazardous product. In that case the agent does **not** fall back to a full-document scan, since doing so would re-introduce the contamination this feature is designed to avoid.

---

## Getting Started

### Prerequisites

- Python 3.9+ (3.12 recommended)
- Access to the Oracle DB that stores the SDS PDFs (connection is over the network; `python-oracledb` runs in thin mode, so no Oracle client install is required in most cases)
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) installed, with its path set in `.env` (`TESSERACT_PATH`). Make sure the `eng` language pack is present (`tesseract --list-langs`).
- Access to CISPro and an OpenRouter API key

### Installation

```bash
git clone https://mygitlab.irbm.it/data-science/sds-parser-daily-agent.git
cd SDS_Parser

pip install -r requirements.txt
```

For a server deployment, install into a **dedicated environment** rather than the system Python:

```bash
# venv
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# …or conda
conda create -p /path/to/envs/sds_parser python=3.12 -y
conda activate /path/to/envs/sds_parser
pip install -r requirements.txt
```

---

## Configuration

All configuration and secrets live in a **single `.env` file** at the project root. `config.py` no longer holds any values — it simply loads `.env` (via `python-dotenv`) and exposes the settings to the rest of the code through `os.environ`. This keeps a single sensitive file to protect, and means `config.py` can now be safely committed.

### `.env` — everything in one place

```
# --- Oracle Database (used to fetch SDS PDFs) ---
DB_USERNAME=your_oracle_user
DB_PASSWORD=your_oracle_password
DB_HOST=your_oracle_host
DB_PORT=1521
DB_SERVICE=BIOVIA

# --- CISPro REST API login (used by the agent to upload results) ---
CISPRO_USERNAME=your_cispro_login
CISPRO_PASSWORD=your_cispro_password

# --- CISPro API endpoints ---
LOGIN_URL=https://your-cispro-host:9953/foundation/hub/api/v1/security/login
LOGOUT_URL=https://your-cispro-host:9953/foundation/hub/api/v1/security/logout
DELETE_URL=https://your-cispro-host/cispro/inventory/api/v1/session
UPLOAD_URL=https://your-cispro-host/cispro/inventory/api/v1/ghs
UPLOAD_ADDITIONAL_URL=https://your-cispro-host/cispro/inventory/api/v1/chemicals

# --- OpenRouter (LLM API) ---
OPENROUTER_API_KEY=sk-or-v1-...
LLM_MODEL_NAME=moonshotai/kimi-k2.5     # optional default; --model on the CLI overrides it

# --- Tesseract OCR + reference dictionary ---
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe   # Linux: /usr/bin/tesseract
TSV_PATH=ghscode_10.txt
```

> ⚠️ Two distinct credential sets — don't confuse them:
> - `DB_USERNAME` / `DB_PASSWORD` → the **Oracle** connection that stores the SDS PDFs.
> - `CISPRO_USERNAME` / `CISPRO_PASSWORD` → the **CISPro REST API** login used to upload results.

> Keep `.env` out of version control (add it to `.gitignore`) and restrict its permissions on the server (`chmod 600 .env`, or NTFS permissions on Windows). Commit a `.env.example` with the same keys and empty values to document what needs filling in.

### Choosing the LLM model

Available model strings are listed in `models.txt`. Pick one and pass it via `--model`, or set `LLM_MODEL_NAME` in `.env` as the default. The CLI flag always takes priority over the `.env` value.

---

## Usage

### Interactive mode (Gradio UI)

1. Open `gradio_app/SDS_parser_gradio.ipynb` in Jupyter.
2. Run all cells — a Gradio web interface launches in your browser.
3. Enter a material ID (Z-number) and click **Analyze**.
4. Inspect the JSON output and submit to CISPro with a click.

> **Working directory** — the notebook lives in `gradio_app/`, but the shared core and the relative paths (`ghscode_10.txt`, `.env`, `models.txt`) are resolved from the project root. The first notebook cell therefore `chdir`s back to the root **and** adds both the root and `gradio_app/` to `sys.path`, so imports and file lookups both work.

> **Gradio version** — pinned to `5.37.*`. Gradio 6.x changed how `gr.Code` renders and broke the JSON output panels; stay on 5.37 unless the callbacks are migrated to `gr.JSON`.

### Daily agent (unattended)

```bash
# Discover today's new materials, no LLM (H-code extraction only)
python -m agent.daily_agent

# Discover + LLM extraction (model from .env)
python -m agent.daily_agent --llm

# Discover + LLM, explicit model (string from models.txt)
python -m agent.daily_agent --llm --model moonshotai/kimi-k2.5

# Custom discovery query from a .sql file
python -m agent.daily_agent --sql queries/today_materials.sql --llm
```

### Fixed-list mode (replaces `batch_runner.py`)

Feed an explicit list instead of the daily DB discovery. Same engine, same artefacts.

```bash
# Process a fixed list of Z-numbers (one per line)
echo -e "Z0132456\nZ0132457\nZ0131000" > ids.txt

python -m agent.daily_agent --ids ids.txt --llm --model moonshotai/kimi-k2.5

# Re-run the same list the same day: bypass idempotency to reprocess everything
python -m agent.daily_agent --ids ids.txt --llm --force
```

### CLI options

| Option | Description |
|---|---|
| `--llm` | Enable LLM-based additional-data extraction via OpenRouter. |
| `--model NAME` | LLM model name; overrides `LLM_MODEL_NAME` from `.env`. |
| `--ids FILE` | Process Z-numbers from a file (one per line) instead of DB discovery. |
| `--sql FILE` | Use a custom discovery query from a `.sql` file (ignored when `--ids` is set). |
| `--force` | Skip the idempotency check (reprocess IDs already done today). |

---

## Run Artefacts

Each agent run writes **everything into a single per-run folder**, `runs/run_<run_id>/`, which keeps inspection simple. The only file kept outside is `agent_state.json` (the idempotency state), which is shared across runs.

```
SDS_Parser/
├── runs/
│   └── run_20260618080001/
│       ├── agent_20260618080001.log        # full run log
│       ├── ids_20260618.txt                # Z-numbers processed in this run
│       ├── Z0132456.json                   # main JSON (local backup)
│       ├── Z0132456_additional.json        # additional JSON (when --llm)
│       ├── ...
│       ├── shadow_section2.jsonl           # section-2 vs full-document comparison (audit)
│       ├── trace_20260618080001.jsonl      # JSONL trajectory (one object per step)
│       └── summary_20260618080001.json     # run summary
└── agent_state.json                        # idempotency state (auto-managed, shared)
```

### Example summary JSON

```json
{
  "run_id": "20260618080001",
  "run_date": "2026-06-18",
  "statistics": {
    "discovered": 5,
    "new_to_process": 4,
    "processed_ok": 4,
    "failed": 0,
    "skipped_already_done": 1
  },
  "failed_ids": [],
  "llm_enabled": true,
  "model": "moonshotai/kimi-k2.5"
}
```

### Agent behaviour on incidents

| Situation | Agent behaviour |
|---|---|
| No materials created today | Clean exit, no per-material artefacts produced. |
| All IDs already processed (double run) | Clean exit thanks to `agent_state.json`. |
| Crash mid-batch | Only unmarked IDs are retried on the next run. |
| Error on a single ID | Logged + counted in `failed_ids`; other IDs continue. |
| CISPro API error | Logged per ID; no batch interruption. |
| Missing credentials | Immediate `EnvironmentError` before any action. |

---

## Scheduling

The scheduled job runs the **agent**, not the UI. In both cases below, make sure the **working directory is the project root** so the relative paths (`ghscode_10.txt`, `.env`, `models.txt`) resolve, and call the **environment's Python by absolute path** (no need to activate the venv/conda env).

### Windows Task Scheduler

Use **Create Task…** (not *Create Basic Task*, which hides the options you need).

1. **General**: tick *Run whether user is logged on or not*; check the run-as account can read `E:\SDS_Parser`, `.env` and Tesseract.
2. **Triggers**: Daily at **07:30**.
3. **Actions** → *Start a program*:
   - **Program/script**: `E:\Anaconda\Miniconda3\envs\sds_parser\python.exe`
   - **Add arguments**: `-m agent.daily_agent --llm`
   - **Start in**: `E:\SDS_Parser`   ← **required** (sets the working directory)
4. **Conditions**: *Start only if a network connection is available*.
5. **Settings**: *Run task as soon as possible after a scheduled start is missed*.

Test it immediately with right-click → **Run**, then confirm a new `runs\run_<run_id>\` folder appears with a green summary (and that the task's last-run result is `0x0`).

### Linux / cron

```cron
30 7 * * * cd /opt/sds_parser && /opt/sds_parser/.venv/bin/python -m agent.daily_agent --llm >> runs/cron.log 2>&1
```

---

## GHS Reference Dictionary

`ghscode_10.txt` is a tab-separated file covering the full GHS hazard-statement catalogue. Its content is based on the official **UN GHS Rev. 10** publication:
<https://unece.org/transport/documents/2023/07/standards/ghs-rev10>

For each H-code it maps:

| Column | Description |
|---|---|
| H-Code | e.g. `H301` |
| Hazard Statement | e.g. *Toxic if swallowed* |
| GHS Hazard Class | e.g. *Acute toxicity, oral* |
| GHS Hazard Category | e.g. *Category 3* |
| GHS Pictogram | e.g. `GHS06` |
| GHS Signal Word | `Danger` / `Warning` |
| P-Code(s) | Associated precautionary statements |
| CISPro classification / pictogram / signal word Id | Internal CISPro labels |

---

## Key Functions (`SDS_functions.py`)

| Function | Description |
|---|---|
| `connect_to_CISPro_api(user, pwd)` | Authenticate to CISPro, return bearer token. |
| `disconnect_from_CISPro_API()` | Log out and clear the token. |
| `get_oracle_connection()` | Open an Oracle DB connection. |
| `get_nodeid_from_material_id(conn, znumber)` | Resolve a Z-number to its node ID. |
| `get_pdf_blob_from_db(conn, nodeid)` | Fetch the SDS PDF binary from Oracle. |
| `assess_text_quality(doc)` | Heuristic deciding text-extraction vs OCR. |
| `perform_ocr_on_pdf(pdf_bytes)` | Run Tesseract OCR as a fallback. |
| `read_tsv_file(tsv_path)` | Load the GHS reference dictionary. |
| `search_h_codes_in_pdf(text, dict, nodeid)` | Match H-codes (scoped to section 2 when available) and build the main JSON. |
| `analyser_material_id(mid, dict)` | End-to-end analysis for one material; returns `(json, full_text)`. |
| `build_additional_json(result, text, model, key)` | Build the additional JSON (rule-based PPE + optional LLM data). |
| `envoyer_json(user, pwd, json)` | POST the main JSON to the CISPro GHS endpoint. |
| `send_additional_json(user, pwd, json)` | PUT the additional JSON to the CISPro chemicals endpoint. |

---

## Notes & Limitations

- **OCR quality** — accuracy on scanned PDFs depends on scan resolution and document quality.
- **LLM availability** — OpenRouter models (especially free tiers) may have rate limits or variable availability; results may vary by model. Some reasoning models return their answer in a `reasoning` field rather than `content`; the parser handles both.
- **H-code casing** — the input text is **not** upper-cased before matching, because suffix casing carries meaning (`H360Fd` ≠ `H360FD`). A non-standard SDS using a lowercase `h` prefix would not be matched, but this is extremely rare under GHS convention.
- **Section-2 detection** — relies on the numbered-section structure of GHS/CLP SDSs. A badly malformed or heavily OCR-degraded document where section boundaries can't be found falls back to a full-document scan (and the `shadow_section2.jsonl` audit lets you spot such cases).
- **Gradio** — pinned to `5.37.*`; newer 6.x releases changed `gr.Code` rendering.
- **No medical/safety advice** — this tool is for data processing only; always consult the original SDS for safety decisions.

---

## License

Internal tool — please check with the project owner before reuse or redistribution.
