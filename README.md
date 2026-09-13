# SDS Parser 🧪
### Automated Safety Data Sheet Analysis & GHS Data Extraction

---

## Overview

**SDS Parser** is a Python tool for automated extraction and classification of chemical hazard data from Safety Data Sheet (SDS) documents. Given a material identifier (Z-number), the tool retrieves the corresponding SDS PDF from an Oracle database, extracts GHS-compliant hazard information, and uploads the structured output directly to a **CISPro** (Biovia) chemical inventory system via REST API.

The tool can be used three ways:

- **Interactive mode** — a Gradio web interface (embedded in a Jupyter Notebook) for single or batch analysis.
- **Daily agent** — an unattended, stateful pipeline that discovers each day's new materials, processes them, and writes full run artefacts. Safe to schedule and safe to restart after a crash.
- **Fixed-list mode** — the same agent, fed an explicit `ids.txt` instead of the daily DB discovery (this replaces the former standalone `batch_runner.py`).

Any of the agent modes can be run with `--dry-run`, which performs the full extraction but writes nothing to CISPro — see *Dry run* below.

---

## Features

- 📄 **PDF text extraction** — PyMuPDF (`fitz`) for fast parsing of digital SDS PDFs.
- 🔍 **OCR fallback** — automatic switch to Tesseract OCR when the text layer is missing or poor quality (scanned PDFs). The choice is driven by a text-quality heuristic (`assess_text_quality`).
- ⚠️ **GHS hazard extraction** — identifies H-codes, GHS pictograms and signal words against a curated reference dictionary (`ghscode_10.txt`) covering the full UN GHS catalogue. H-code suffix casing is preserved, so `H360Fd` (*suspected* of damaging the unborn child) is never conflated with `H360FD` (*may* damage it).
- 🇪🇺 **EUH statements (EU CLP)** — optional detection of the supplemental hazard statements defined by Regulation (EC) No 1272/2008, which are **not** part of the UN GHS catalogue (`EUH019`, `EUH066`, …). Kept in a separate reference file and a separate code path, and shipped in *audit* mode so occurrences are measured before anything reaches CISPro (see *EUH statements* below).
- 🎯 **Section-2 scoping** — H-code search is restricted to **section 2** of the SDS (the authoritative GHS/CLP product classification section) when that section can be located, and falls back to a full-document scan otherwise. This avoids false positives from component tables (section 3), toxicological data (section 11) and glossaries (section 16). Each run also stores a parallel comparison between the section-2 result and the full-document result (see *Section-2 scoping* below).
- 🤖 **LLM-assisted extraction** — a single, structured-output call to a **self-hosted [Ollama](https://ollama.com/) server** (no data leaves the internal network), validated by Pydantic, extracts physicochemical properties (physical state, boiling point, flash point) and a storage & handling summary.
- 📦 **Dual JSON output**:
  - **Main JSON** — GHS hazard classification (label codes, pictograms, signal word).
  - **Additional JSON** — PPE recommendations, physicochemical data, storage & handling.
- 🔗 **CISPro integration** — uploads both JSONs to CISPro via authenticated REST API.
- 🗂️ **Stateful agent** — idempotency (no double-processing), per-ID crash safety, JSONL trajectory log and JSON run summary.
- 🧪 **Dry run** — `--dry-run` runs the whole extraction chain and writes every artefact locally, with **no write to CISPro** and no change to the idempotency state. Intended for reviewing extraction quality before a real upload.
- 🖥️ **Gradio UI** — interactive analysis without writing any code.

---

## Tech Stack

| Component | Technology |
|---|---|
| PDF text extraction | [PyMuPDF](https://pymupdf.readthedocs.io/) (`fitz`) |
| OCR fallback | [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) + [pytesseract](https://github.com/madmaze/pytesseract) |
| Database | Oracle DB via [python-oracledb](https://python-oracledb.readthedocs.io/) |
| LLM calls | [Ollama](https://ollama.com/) — self-hosted on an internal server (configurable model) |
| Schema validation | [Pydantic](https://docs.pydantic.dev/) v2 |
| Config / secrets | [python-dotenv](https://github.com/theskumar/python-dotenv) |
| Web interface | [Gradio](https://www.gradio.app/) (pinned to `5.37.*`) |
| Chemical inventory | CISPro (Biovia) REST API |
| Data handling | pandas, chardet |
| Language | Python 3.9+ (developed/deployed on 3.12) |

---

## Project Structure

A **single project**, organised by folder. The shared core (`SDS_functions.py`, `config.py`, `llm_extraction.py`, `section2_isolation.py`, `euh_codes.py` and the two reference TSVs) stays at the root because both the UI and the agent import it — keeping one copy avoids divergence.

```
SDS_Parser/
├── SDS_functions.py            # Core logic: DB access, PDF parsing, OCR, H-code extraction, LLM calls, API upload
├── llm_extraction.py           # Single structured-output Ollama call + Pydantic validation (SDSExtraction)
├── config.py                   # Thin reader: loads .env and exposes the settings to the rest of the code
├── ghscode_10.txt              # Reference TSV: H-codes → GHS categories, pictograms, signal words, P-codes
├── section2_isolation.py       # Section-2 isolation + shadow audit (section 2 vs full document)
├── euh_codes.py                # EUH detection (EU CLP): loader, regex, mode, audit trail
├── euh_codes_clp.txt           # Reference TSV: EUH supplemental statements (CLP Annexes II/III)
├── models.txt                  # Ollama model tags available on the internal server
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
 │  + EUH (optional)       │  → label codes, pictograms, signal word
 │                         │  EUH: label codes only, never pictogram/signal word
 └──────────┬──────────────┘
            │
            ├──────────────────────────────────────────►
            │                               ┌───────────────────────────────┐
            │                               │  LLM call (Ollama, optional)  │
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

## EUH statements (EU CLP)

A small number of SDSs carry **EUH** statements alongside their H-codes — `EUH019` (*may form explosive peroxides*), `EUH066` (*repeated exposure may cause skin dryness or cracking*), and so on. These come from **Regulation (EC) No 1272/2008 (CLP)**, Annexes II and III. They are an **EU addition and are absent from the UN GHS catalogue**, which is why they cannot live in `ghscode_10.txt`: that file's value rests on being traceable to a single UN publication.

They are therefore handled by a dedicated module (`euh_codes.py`) and a dedicated reference file (`euh_codes_clp.txt`).

### Why they are kept off the GHS code path

Under CLP an EUH statement is *supplemental information*, not a classification: it carries **no pictogram and no signal word**. Folding EUH entries into the H-code dictionary would push empty values into the pictogram set, the signal-word priority rule and the GHS07 suppression rules. The implementation keeps them in their own set and merges them **only** into `labelCodes`; `pictograms`, `classifications` and `signalWord_id` are still derived exclusively from the H-codes.

There is no regex collision to worry about: `\bH\d{3}` does not match the `H` inside `EUH019`, since `U` and `H` are both word characters and there is no word boundary between them. EUH codes were silently ignored before this feature, never mis-read as H-codes.

### Three modes — `EUH_MODE`

| Mode | Behaviour |
|---|---|
| `off` | No detection at all. Byte-identical to the pre-EUH behaviour. |
| `audit` | **Default.** Codes are detected and written to `euh_audit.jsonl`, but **not** added to `labelCodes`. Nothing changes in what is POSTed to CISPro. |
| `on` | Codes are detected **and** added to `labelCodes`. |

The mode is read from `EUH_MODE` in `.env`. `--euh-mode` overrides it for a single run, and pairs naturally with `--dry-run`:

```bash
# Preview exactly what "on" would put in labelCodes, without touching CISPro
python -m agent.daily_agent --ids ids.txt --euh-mode on --dry-run --force
```

That combination is the intended validation path: inspect the `labelCodes` produced in `runs/run_<run_id>_dryrun/`, then try a single real POST on a sandbox material, and only then change `.env`. An unknown value passed to `--euh-mode` is rejected by `argparse` before any material is touched; an unknown value in `.env` degrades to `off` with a warning rather than being guessed at.

The default is deliberately `audit`. Before switching to `on`, confirm that the CISPro GHS jurisdiction (`31745`) actually accepts an EUH value in `labelCodes` — a jurisdiction typed `GHS` may not know the EU vocabulary, and an unknown code can be rejected or, worse, silently dropped. Note that `--dry-run` cannot answer this question on its own: it validates the extraction, not what CISPro does with the payload.

Running in `audit` for a few nights also answers the question that decides whether the feature is worth turning on at all:

```bash
# occurrences per code
jq -r '.known[]' euh_audit.jsonl | sort | uniq -c | sort -rn

# codes seen in the corpus but not enabled in the reference file
jq -r 'select(.unknown | length > 0) | "\(.material_id) \(.unknown|join(","))"' euh_audit.jsonl
```

### Reference file — `euh_codes_clp.txt`

Tab-separated, same loading convention as `ghscode_10.txt`, with an `Include` column that switches a code on or off without touching any code. Lines starting with `#` are comments.

| Column | Description |
|---|---|
| EUH-Code | e.g. `EUH019` (the letter suffix of `EUH201A` / `EUH209A` is part of the code) |
| Hazard Statement (EN) | e.g. *May form explosive peroxides* |
| Type | `Physical` / `Health` / `Environment` / `Label element` |
| CLP Reference | e.g. *Annex III Part 2, Table 2.1* |
| Include | `TRUE` → detected, `FALSE` → ignored |
| Notes | Free text (deletions, superseded codes, practical remarks) |

As shipped, **20 codes are enabled and 15 are not**:

- **Enabled** — the physical and health statements of Annex III Part 2 (`EUH001`, `EUH014`, `EUH018`, `EUH019`, `EUH029`, `EUH031`, `EUH032`, `EUH044`, `EUH066`, `EUH070`, `EUH071`), the legacy `EUH059`, and the endocrine-disruption / PBT / vPvB / PMT statements introduced by Regulation (EU) 2023/707 (`EUH380`, `EUH381`, `EUH430`, `EUH431`, `EUH440`, `EUH441`, `EUH450`, `EUH451`). These describe real reactivity or health hazards and are directly useful to an inventory.
- **Disabled** — the supplemental *label elements* of Annex III Part 3 (`EUH201`–`EUH212`) and `EUH401`. These are consumer-facing labelling boilerplate; `EUH208` in particular would appear on a large share of mixtures and add noise to `labelCodes`. Flip `Include` to `TRUE` if the CISPro users ask for them.

A code that matches the EUH pattern but is absent from the file (or set to `FALSE`) is **never emitted**. It is reported as a `WARNING` in the run log and recorded under `unknown` in the audit file, so a newly published statement appearing in the corpus does not pass unnoticed.

### Detection details

- The pattern tolerates `EUH019`, `EUH 019` and `EUH-019`, which covers OCR and layout artefacts, and preserves the letter suffix of `EUH201A` / `EUH209A`.
- No whitespace is allowed before the suffix, so `EUH210 Available on request` is read as `EUH210` and not as `EUH210A`.
- Detection is scoped to **section 2** exactly like the H-codes, and benefits from the same isolation. A section 2 containing only EUH statements now scores the same confidence as one containing H-codes, so it no longer falls back to a full-document scan.
- A product carrying EUH statements and **no H-code at all** is valid (a solvent labelled only `EUH019`). In `on` mode its codes are reported with `signalWord_id` left at *none* — EUH statements never carry a signal word.

---

## LLM backend — Ollama (self-hosted)

The optional LLM enrichment runs against an **[Ollama](https://ollama.com/) server hosted on the internal network**. No SDS content leaves the corporate perimeter, and there is no API key or per-token cost to manage.

> Note on scope — the LLM is deliberately **not** involved in H-code identification, which stays fully deterministic (regex + section-2 isolation). It is used only for the free-text fields of the additional JSON: physical state, boiling point, flash point and the storage & handling summary. PPE and the `hazardous` flag remain rule-based and are produced even without `--llm`.

### Why the native endpoint

`llm_extraction.py` targets Ollama's **native `/api/chat`** endpoint rather than its OpenAI-compatible `/v1/chat/completions` one. The reason is `num_ctx`: only the native endpoint lets the context window be set **per request**.

This matters more than it looks. Ollama's default context is **2048 tokens** — far shorter than a full SDS. When a prompt exceeds it, Ollama **silently truncates** the input: no error, no warning, just a degraded extraction that is hard to spot in a nightly batch. The context size is therefore set explicitly on every call (`OLLAMA_NUM_CTX`, default `8192`).

The call also uses:

| Setting | Value | Purpose |
|---|---|---|
| `format` | JSON Schema of `SDSExtraction` | Constrains the model to the exact output object — removes most parsing failures. |
| `options.temperature` | `0` | Deterministic output, appropriate for an unattended batch. |
| `options.num_ctx` | `OLLAMA_NUM_CTX` | Prevents silent truncation (see above). |
| `options.num_thread` | `OLLAMA_NUM_THREAD` | CPU threads. Omitted from the payload when `0` (default), letting Ollama choose. |
| `stream` | `false` | Single JSON response; the reply is read from `message.content`. |

Any failure (server unreachable, timeout, malformed JSON, validation error) is caught and returns an **all-`None` `SDSExtraction`**, so the caller can always rely on the four fields existing and one bad document never aborts a run.

### Server setup

On the Ollama host, pull the models you want to expose and confirm they are listed:

```bash
ollama pull qwen3:8b
ollama list
```

Then make sure the server is reachable from the SDS Parser host (adjust to your network):

```bash
curl http://<ollama-host>:11434/api/tags
```

> **Binding** — by default Ollama listens on `127.0.0.1` only. To accept calls from another machine, set `OLLAMA_HOST=0.0.0.0:11434` on the **server** and open the port in the firewall.

`models.txt` lists the tags offered in the Gradio dropdown; keep it in sync with `ollama list`. It must contain **one bare tag per line and no comment lines** — the notebook filters empty lines only, so a `#` line would appear as a selectable entry.

### CPU-only tuning

The current target is a Windows Server with 32 GB RAM and **no GPU**, so inference is CPU-bound and a single SDS can take tens of seconds. Points worth checking on the server:

- **Thread count** — ⚠️ **There is no `OLLAMA_NUM_THREAD` server environment variable.** Despite being widely repeated in third-party guides, it is not in Ollama's `envconfig` and setting it has no effect (it is a long-standing open feature request, [ollama#9784](https://github.com/ollama/ollama/issues/9784)). Thread count is a **per-request** option, `options.num_thread`, which this project exposes through `OLLAMA_NUM_THREAD` in `.env` (default `0` = let Ollama decide, and the key is then omitted from the payload entirely). Measure before overriding.
- **Model size** — prefer a 4-bit quantized model of ≤ 8B parameters. `qwen3:8b` (Q4) is a reasonable starting point; on a 4-core host, `qwen3:4b` is often the better trade-off.
- **`OLLAMA_TIMEOUT`** — default `300` s, deliberately generous for CPU inference. Raise it if you see timeouts in the run log rather than assuming the model failed. (This one is a **project** variable read by `config.py`, not an Ollama variable.)
- **`OLLAMA_CONTEXT_LENGTH`** — this *is* a real Ollama server variable, setting the default `num_ctx`. The project already sets `num_ctx` per request, so it is not required; setting it on the server is a useful belt-and-braces guard against the 2048 default.
- **Windows specifics** — add a Defender exclusion for the model directory, use the *High Performance* power plan, and store models on an SSD.

> **Naming trap** — `OLLAMA_NUM_THREAD` in this project's `.env` is a *project* setting consumed by `config.py` and forwarded as `options.num_thread`. It is deliberately named after the API option, but it is **not** read by the Ollama server itself. Setting it in the server's environment does nothing.

### Rolling back to a cloud provider

The previous OpenRouter configuration is kept **commented out** in `config.py` rather than deleted. Switching back means restoring those lines and reinstating the API-key argument in `llm_extraction.py` — the `SDSExtraction` model, the prompt and every call site are provider-agnostic and stay unchanged.

---

## Getting Started

### Prerequisites

- Python 3.9+ (3.12 recommended)
- Access to the Oracle DB that stores the SDS PDFs (connection is over the network; `python-oracledb` runs in thin mode, so no Oracle client install is required in most cases)
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) installed, with its path set in `.env` (`TESSERACT_PATH`). Make sure the `eng` language pack is present (`tesseract --list-langs`).
- Access to CISPro
- Network access to the internal **Ollama** server (only needed for the optional `--llm` enrichment), with at least one model pulled — see *LLM backend* above

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

# --- Ollama (internal LLM server) ---
OLLAMA_BASE_URL=http://your-ollama-host:11434   # default http://localhost:11434
OLLAMA_NUM_CTX=8192                             # context window; MUST stay well above Ollama's 2048 default
OLLAMA_TIMEOUT=300                              # seconds; generous because inference is CPU-only
OLLAMA_NUM_THREAD=0                             # 0 = let Ollama decide; see "CPU-only tuning" before changing
LLM_MODEL_NAME=qwen3:8b                         # optional default; --model on the CLI overrides it

# --- Tesseract OCR + reference dictionaries ---
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe   # Linux: /usr/bin/tesseract
TSV_PATH=ghscode_10.txt

# --- EUH statements (EU CLP) ---
EUH_TSV_PATH=euh_codes_clp.txt                  # CLP reference; kept separate from the UN GHS file
EUH_MODE=audit                                  # off | audit | on — overridable per run with --euh-mode
```

> ⚠️ Two distinct credential sets — don't confuse them:
> - `DB_USERNAME` / `DB_PASSWORD` → the **Oracle** connection that stores the SDS PDFs.
> - `CISPRO_USERNAME` / `CISPRO_PASSWORD` → the **CISPro REST API** login used to upload results.

> Keep `.env` out of version control (add it to `.gitignore`) and restrict its permissions on the server (`chmod 600 .env`, or NTFS permissions on Windows). Commit a `.env.example` with the same keys and empty values to document what needs filling in.

### Choosing the LLM model

Available model tags are listed in `models.txt`. Pick one and pass it via `--model`, or set `LLM_MODEL_NAME` in `.env` as the default. The CLI flag always takes priority over the `.env` value.

> ⚠️ The tag must correspond to a model **already pulled on the Ollama server** (`ollama list`). Ollama tags look like `qwen3:8b`, not like the provider-prefixed strings used by cloud gateways (`moonshotai/kimi-k2.5`). An unknown tag makes the call fail; the run continues and the additional JSON simply comes back with `null` fields, with the error recorded in the run log.

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

# Discover + LLM, explicit model (tag from models.txt)
python -m agent.daily_agent --llm --model qwen3:8b

# Custom discovery query from a .sql file
python -m agent.daily_agent --sql queries/today_materials.sql --llm

# Add EUH codes to labelCodes for this run only (overrides EUH_MODE from .env)
python -m agent.daily_agent --euh-mode on

# Turn EUH detection off entirely for this run
python -m agent.daily_agent --euh-mode off
```

### Fixed-list mode (replaces `batch_runner.py`)

Feed an explicit list instead of the daily DB discovery. Same engine, same artefacts.

```bash
# Process a fixed list of Z-numbers (one per line)
echo -e "Z0132456\nZ0132457\nZ0131000" > ids.txt

python -m agent.daily_agent --ids ids.txt --llm --model qwen3:8b

# Re-run the same list the same day: bypass idempotency to reprocess everything
python -m agent.daily_agent --ids ids.txt --llm --force
```

### Dry run (extraction only, nothing written to CISPro)

`--dry-run` executes the **complete pipeline** — Oracle read, PDF parsing, OCR fallback, section-2 scoping, H-code extraction, optional LLM enrichment — and writes every JSON artefact to disk, but performs **no write to CISPro**:

| | Normal run | `--dry-run` |
|---|---|---|
| Oracle read (SDS PDF) | ✅ | ✅ (read-only in both cases) |
| Local JSON artefacts | ✅ | ✅ |
| LLM call (with `--llm`) | ✅ | ✅ |
| CISPro login / logout | ✅ | ⛔ skipped |
| `POST` main JSON | ✅ | ⛔ skipped |
| `PUT` additional JSON | ✅ | ⛔ skipped |
| `agent_state.json` updated | ✅ | ⛔ untouched |

Two consequences worth knowing:

- **A dry run never consumes an ID.** Because `mark_processed()` is not called, the material is still picked up by the real run of the same day — a dry run can never silently cause a skipped upload.
- **A dry run works without CISPro.** No session is opened, so the run completes even if the API is down or the credentials are absent (missing `CISPRO_*` values downgrade from a hard error to a warning). The Oracle credentials are still required.

```bash
# Review a fixed list end-to-end without touching CISPro
python -m agent.daily_agent --ids ids.txt --llm --dry-run --force

# Same, against today's discovery query
python -m agent.daily_agent --llm --dry-run

# Preview what EUH_MODE=on would add to labelCodes, without touching CISPro
python -m agent.daily_agent --ids ids.txt --euh-mode on --dry-run --force
```

`--force` is almost always wanted alongside `--dry-run`, so the same batch can be replayed as many times as needed while tuning a prompt or a model.

> **Tip** — for a first check of the wiring, run without `--llm`. The pipeline completes in seconds instead of waiting on CPU inference, and still exercises the H-code path and the skipped-upload logic.

#### Reading the output of a dry run

- Artefacts land in `runs/run_<run_id>_dryrun/`, never mixed with production runs.
- `summary_<run_id>.json` carries `"dry_run": true`. In that case `processed_ok` means **extracted OK**, *not* **sent to CISPro** — never read the counters without checking the flag.
- In `trace_<run_id>.jsonl`, `main_json_posted` and `additional_json_posted` are `null` (*not attempted*) rather than `false` (*call made and rejected*).
- The `shadow_section2.jsonl` audit is produced as usual, so a dry run is also the natural way to compare section-2 scoping against a full-document scan on a reference set before changing anything in production.
- `euh_audit.jsonl` is likewise appended to, so `--euh-mode on --dry-run` lets you review the EUH codes that *would* be uploaded before committing to them.

### CLI options

| Option | Description |
|---|---|
| `--llm` | Enable LLM-based additional-data extraction via the internal Ollama server. |
| `--model NAME` | LLM model name; overrides `LLM_MODEL_NAME` from `.env`. |
| `--ids FILE` | Process Z-numbers from a file (one per line) instead of DB discovery. |
| `--sql FILE` | Use a custom discovery query from a `.sql` file (ignored when `--ids` is set). |
| `--force` | Skip the idempotency check (reprocess IDs already done today). |
| `--dry-run` | Run the full extraction and write the JSON artefacts locally, but send nothing to CISPro and leave `agent_state.json` untouched. Usually combined with `--force`. |
| `--euh-mode MODE` | EUH handling for this run: `off` \| `audit` \| `on`. Overrides `EUH_MODE` from `.env`. Pairs well with `--dry-run`. |

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
│       ├── trace_20260618080001.jsonl      # JSONL trajectory (one object per step)
│       └── summary_20260618080001.json     # run summary
├── agent_state.json                        # idempotency state (auto-managed, shared)
├── shadow_section2.jsonl                   # section-2 vs full-document comparison (audit, cumulative)
└── euh_audit.jsonl                         # EUH statements detected per material (audit, cumulative)
```

> **Audit trails live at the root, not in the run folder.** Both `*.jsonl` audit files are written to the **working directory** (the project root) and **accumulate across runs** — that is what makes them useful for trend analysis. Each record carries a timestamp and a material ID, so a single run can still be isolated after the fact. Rotate or archive them periodically; nothing prunes them automatically.

Dry runs use the same layout under a suffixed folder, `runs/run_<run_id>_dryrun/`, so review artefacts are never confused with the artefacts of a real upload.

### Example summary JSON

```json
{
  "run_id": "20260618080001",
  "run_date": "2026-06-18",
  "dry_run": false,
  "statistics": {
    "discovered": 5,
    "new_to_process": 4,
    "processed_ok": 4,
    "failed": 0,
    "skipped_already_done": 1
  },
  "failed_ids": [],
  "llm_enabled": true,
  "model": "qwen3:8b"
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
| Missing credentials | Immediate `EnvironmentError` before any action — downgraded to a warning for the CISPro credentials under `--dry-run`, since no API call is made. |
| Run launched with `--dry-run` | Artefacts written to `runs/run_<run_id>_dryrun/`; nothing uploaded; the IDs stay pending for the real run. |

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

> The EU **EUH** statements are *not* in this file. They come from a different regulator and a different document (CLP, not UN GHS), and live in `euh_codes_clp.txt` — see *EUH statements (EU CLP)* above. Keeping the two apart is what makes each file's provenance auditable.

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
| `search_h_codes_in_pdf(text, dict, nodeid, euh_dict=None)` | Match H-codes (scoped to section 2 when available), optionally add EUH codes, and build the main JSON. |
| `analyser_material_id(mid, dict)` | End-to-end analysis for one material; returns `(json, full_text)`. |
| `build_additional_json(result, text, model, use_llm)` | Build the additional JSON (rule-based PPE + optional Ollama data). |
| `envoyer_json(user, pwd, json)` | POST the main JSON to the CISPro GHS endpoint. |
| `send_additional_json(user, pwd, json)` | PUT the additional JSON to the CISPro chemicals endpoint. |

### `euh_codes.py`

| Function | Description |
|---|---|
| `get_euh_mode()` | Active mode: the CLI override when set, otherwise `EUH_MODE` from `.env`. |
| `set_euh_mode(mode)` | Apply a CLI override for the process; `None` clears it. Raises `ValueError` on an unknown mode. |
| `read_euh_tsv_file(tsv_path)` | Load the CLP reference, keeping only rows with `Include=TRUE`. |
| `get_euh_dict()` | Lazy per-process cache of the above, read from `EUH_TSV_PATH`. |
| `reset_euh_dict_cache()` | Force a reload — useful in tests and in a long-lived Gradio session. |
| `find_euh_codes(text, euh_dict=None)` | Return `(known, unknown)` sets of EUH codes found in *text*. |
| `log_euh_findings(mid, known, unknown, mode, scope)` | Write the log line and the `euh_audit.jsonl` record. |

A missing or unreadable `euh_codes_clp.txt` is **not fatal**: an empty dictionary is returned, a warning is logged, and the pipeline degrades to the pre-EUH behaviour rather than failing a nightly run.

> **Why the mode goes through `get_euh_mode()`** — `SDS_functions.py` calls it rather than importing `EUH_MODE` as a value. `from config import EUH_MODE` binds the setting at **import time**, before `argparse` has run, which would make `--euh-mode` silently do nothing. Resolving at call time is what makes the flag effective.

---

## Notes & Limitations

- **OCR quality** — accuracy on scanned PDFs depends on scan resolution and document quality.
- **LLM availability** — the Ollama server is a single point of failure for the `--llm` path: if it is down or the model tag is unknown, the additional JSON is still produced but its LLM fields come back `null` (the error is logged, the run continues). Results also vary by model. Some reasoning models wrap their answer in a `<think>…</think>` block; the parser strips it.
- **LLM latency (CPU-only)** — inference runs on CPU, so a single SDS can take tens of seconds. Budget accordingly when sizing the nightly batch, and see *CPU-only tuning* above before assuming the pipeline is stuck.
- **Context truncation** — Ollama truncates silently past `num_ctx`. `OLLAMA_NUM_CTX` is set explicitly for this reason; if you lower it, unusually long SDSs may lose their tail sections (7/9/10) without any error appearing in the log.
- **H-code casing** — the input text is **not** upper-cased before matching, because suffix casing carries meaning (`H360Fd` ≠ `H360FD`). A non-standard SDS using a lowercase `h` prefix would not be matched, but this is extremely rare under GHS convention.
- **Section-2 detection** — relies on the numbered-section structure of GHS/CLP SDSs. A badly malformed or heavily OCR-degraded document where section boundaries can't be found falls back to a full-document scan (and the `shadow_section2.jsonl` audit lets you spot such cases).
- **EUH and CISPro** — `EUH_MODE=on` has **not** been validated against the CISPro vocabulary. Until a manual POST confirms that jurisdiction `31745` accepts an EUH value in `labelCodes`, leave the default `audit`. A rejected POST would fail the material; a silently ignored code would be worse, since the run would report success while the data never landed.
- **EUH coverage** — only codes with `Include=TRUE` in `euh_codes_clp.txt` are emitted. Everything else is logged as `unknown` and dropped. This is intentional (an unreviewed code should not reach the inventory), but it does mean the reference file needs a look whenever CLP is amended.
- **EUH prefix casing** — like the H-codes, the text is not upper-cased before matching, so a lowercase `euh019` would not be found. This follows CLP convention and has not been observed in practice.
- **Audit files grow** — `shadow_section2.jsonl` and `euh_audit.jsonl` are append-only and are never pruned. Archive them periodically on a long-running deployment.
- **Dry-run coverage** — a dry run validates everything *up to* the upload, but by definition not the upload itself. A payload that CISPro would reject (schema change, unknown node ID, expired account) will still look like a success in a dry run. It answers *"is the extraction right?"*, not *"will CISPro accept it?"*.
- **Gradio** — pinned to `5.37.*`; newer 6.x releases changed `gr.Code` rendering.
- **No medical/safety advice** — this tool is for data processing only; always consult the original SDS for safety decisions.

---

## License

Internal tool — please check with the project owner before reuse or redistribution.

