"""
SDS Parser — Daily Agent
========================
Stateful 5-step pipeline that runs fully unattended each day:

  Step 1 · Discover   — query Oracle/CISPro for today's new materials
  Step 2 · Filter     — remove already-processed IDs (idempotency)
  Step 3 · Write      — dump the new IDs to ids/ids_YYYYMMDD.txt
  Step 4 · Process    — run the full SDS pipeline per material (reuses SDS_functions.py)
  Step 5 · Report     — write JSONL trace + JSON summary to runs/

Credentials are read from environment variables (via .env file).
No interactive prompts — safe for unattended scheduled execution.

Dry run mode (--dry-run):
    Runs the complete extraction pipeline (Oracle read, PDF parsing, H-code
    extraction, optional LLM enrichment) and writes every JSON artefact to
    runs/run_<run_id>_dryrun/, but performs NO write to CISPro:
      - no POST of the main JSON, no PUT of the additional JSON
      - no CISPro login/logout (the run works even if the API is down)
      - no update of agent_state.json, so a dry run never blocks the real run
    Oracle access stays read-only in both modes (SELECT queries only).

EUH mode (--euh-mode):
    Overrides EUH_MODE from .env for this run only. Combined with --dry-run it
    is the safe way to preview what "on" would add to labelCodes without
    sending anything to CISPro.

Usage:
    python -m agent.daily_agent                       # without LLM
    python -m agent.daily_agent --llm                 # with LLM extraction
    python -m agent.daily_agent --sql queries/q.sql   # custom SQL file
    python -m agent.daily_agent --llm --dry-run       # extract, never write to CISPro
    python -m agent.daily_agent --euh-mode on         # add EUH codes to labelCodes
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

# Load .env BEFORE importing config / SDS_functions (which read config at import time)
from dotenv import load_dotenv
load_dotenv()

from agent.agent_schema import AgentRunState, MaterialResult, StepStatus
from agent.db_discovery  import discover_today_materials
from agent.reporter      import write_summary, write_trace_log
from agent.state_manager import StateManager
from SDS_functions import (
    analyser_material_id,
    build_additional_json,
    connect_to_CISPro_api,
    disconnect_from_CISPro_API,
    envoyer_json,
    read_tsv_file,
    send_additional_json,
)
from config import TSV_PATH
from euh_codes import VALID_EUH_MODES, set_euh_mode

# ── Directory layout ──────────────────────────────────────────────────────────
RUNS_DIR = Path("runs")


# ─────────────────────────────────────────────────────────────────────────────
# Credential helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_cispro_creds(required: bool = True) -> tuple[str, str]:
    """
    Read CISPro credentials from environment.

    Args:
        required: when False (dry run), missing credentials only produce a
                  warning — no CISPro call will be made anyway.

    Raises:
        EnvironmentError: if credentials are missing and required is True.
    """
    username = os.environ.get("CISPRO_USERNAME", "")
    password = os.environ.get("CISPRO_PASSWORD", "")
    if not username or not password:
        if required:
            raise EnvironmentError(
                "CISPRO_USERNAME and CISPRO_PASSWORD must be set "
                "in the .env file or as environment variables."
            )
        logging.warning(
            "⚠️  CISPro credentials missing — tolerated in dry run mode."
        )
    return username, password


def _get_llm_model() -> str | None:
    """Return the LLM model name from env, or None if not set."""
    return os.environ.get("LLM_MODEL_NAME") or None


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging(run_dir: Path, run_id: str) -> None:
    run_dir.mkdir(exist_ok=True)
    log_file = run_dir / f"agent_{run_id}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.info(f"Log file → {log_file}")


# ─────────────────────────────────────────────────────────────────────────────
# Step helpers
# ─────────────────────────────────────────────────────────────────────────────

def _save_ids_file(ids: list[str], run_dir: Path, run_date: str) -> Path:
    """Write Z-numbers to a dated text file (same one-per-line format as ids.txt)."""
    ids_file = run_dir / f"ids_{run_date.replace('-', '')}.txt"
    ids_file.write_text("\n".join(ids), encoding="utf-8")
    logging.info(f"IDs file → {ids_file}  ({len(ids)} ID(s))")
    return ids_file


def _load_ids_from_file(path: str) -> list[str]:
    """Read Z-numbers from a text file (one per line)."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    ids = [l.strip() for l in lines if l.strip()]
    logging.info(f"   → {len(ids)} ID(s) loaded from {path}")
    return ids


def _process_one(
    mid: str,
    h_codes_dict: dict,
    username: str,
    password: str,
    output_dir: Path,
    use_llm: bool,
    model_name: str | None,
    dry_run: bool = False,
) -> MaterialResult:
    """
    Run the full SDS pipeline for a single material ID.

    Mirrors the logic of batch_runner.process_material_id() but returns a
    typed MaterialResult instead of relying on side-effects, and never
    raises — all exceptions are caught and stored in result.error.

    When dry_run is True, everything runs exactly the same way except the two
    CISPro calls, which are skipped. The *_json_posted fields are then left at
    None ("not attempted"), which distinguishes them from a real failed POST.
    """
    t0     = time.monotonic()
    result = MaterialResult(material_id=mid, dry_run=dry_run)

    try:
        logging.info(f"  ┌─ {mid}: start")

        # ── Analyse: PDF retrieval + H-code extraction ────────────────────────
        result_json, full_text = analyser_material_id(mid, h_codes_dict)

        if result_json is None:
            result.error = str(full_text)
            logging.error(f"  └─ {mid} ❌  Analysis failed: {full_text}")
            return result

        # ── Save main JSON locally ────────────────────────────────────────────
        main_path = output_dir / f"{mid}.json"
        main_path.write_text(result_json, encoding="utf-8")
        result.main_json_saved = True
        logging.info(f"  │  {mid}: main JSON saved → {main_path.name}")

        # ── POST main JSON to CISPro ──────────────────────────────────────────
        if dry_run:
            logging.info(f"  │  {mid}: [DRY RUN] main JSON not posted to CISPro")
        else:
            resp = envoyer_json(username, password, result_json)
            result.main_json_posted = "✅" in resp or "Success" in resp
            logging.info(f"  │  {mid}: POST main → {resp}")

        # ── Optional LLM: build + POST additional JSON ────────────────────────
        if use_llm and model_name:
            try:
                parsed     = json.loads(result_json)
                additional = build_additional_json(
                    parsed, full_text, model_name, use_llm=True
                )
                if additional:
                    add_path = output_dir / f"{mid}_additional.json"
                    add_path.write_text(
                        json.dumps(additional, indent=4, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    result.additional_json_saved = True

                    if dry_run:
                        logging.info(
                            f"  │  {mid}: [DRY RUN] additional JSON not posted to CISPro"
                        )
                    else:
                        resp2 = send_additional_json(
                            username, password, json.dumps(additional)
                        )
                        result.additional_json_posted = (
                            "✅" in resp2 or "Success" in resp2
                        )
                        logging.info(f"  │  {mid}: POST additional → {resp2}")
            except Exception as exc:
                # LLM failure is non-fatal: log and continue
                logging.error(f"  │  {mid}: LLM step failed — {exc}")

        result.status = StepStatus.SUCCESS
        logging.info(f"  └─ {mid} ✅  done ({round(time.monotonic()-t0, 1)}s)")

    except Exception as exc:
        result.error = str(exc)
        logging.exception(f"  └─ {mid} ❌  exception")

    result.duration_s = round(time.monotonic() - t0, 2)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_agent(
    sql_query: str | None = None,
    use_llm: bool = False,
    ids_file: str | None = None,
    model_override: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """
    Execute the 5-step daily agent pipeline.

    Args:
        sql_query:      Optional SQL text that overrides the default discovery query.
        use_llm:        Whether to call the Ollama LLM for additional extraction.
        ids_file:       Optional path to a file of Z-numbers, bypassing DB discovery.
        model_override: LLM model name taking priority over LLM_MODEL_NAME.
        force:          Ignore the idempotency filter (reprocess today's IDs).
        dry_run:        Run the full extraction but never write to CISPro,
                        and never persist the run in agent_state.json.

    Note: the EUH mode is deliberately not a parameter here. It is process-wide
    state applied by the CLI through euh_codes.set_euh_mode() before this
    function is called, so every material in the batch is treated identically.
    """
    run_date = str(date.today())
    run_id   = datetime.now().strftime("%Y%m%d%H%M%S")

    # Unique folder of the run : all is grouped.
    # Dry runs get their own suffix so review artefacts are never confused
    # with the artefacts of a real production run.
    run_dir = RUNS_DIR / (f"run_{run_id}_dryrun" if dry_run else f"run_{run_id}")
    run_dir.mkdir(parents=True, exist_ok=True)

    _setup_logging(run_dir, run_id)
    logging.info(
        f"SDS Parser Daily Agent  |  run_id={run_id}  |  date={run_date}"
    )
    if dry_run:
        logging.warning(
            "🧪 DRY RUN — extraction only. Nothing will be written to CISPro "
            "and agent_state.json will not be updated."
        )

    username, password = _get_cispro_creds(required=not dry_run)
    model_name         = (model_override or _get_llm_model()) if use_llm else None
    if use_llm and not model_name:
        logging.warning("⚠️  --llm without model : use --model or LLM_MODEL_NAME (.env). "
            "Without model, only the short JSON is generated.")

    state = AgentRunState(
        run_id     = run_id,
        run_date   = run_date,
        start_time = datetime.now().isoformat(),
        use_llm    = use_llm,
        model_name = model_name,
        dry_run    = dry_run,
    )
    state_manager = StateManager()

    try:

        # ── Step 1 · Discover ─────────────────────────────────────────────────
        if ids_file:
            logging.info("━━━ Step 1 · Load IDs from file ━━━━━━━━━━━━━━━━━━━━")
            discovered = _load_ids_from_file(ids_file)
        else:
            logging.info("━━━ Step 1 · Discover ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            discovered = discover_today_materials(sql_query)
        state.discovered_ids = discovered
        state.log_step("discover", StepStatus.SUCCESS, {
            "count": len(discovered),
            "ids":   discovered,
            "source": ids_file or "db",
        })

        if not discovered:
            logging.info("No materials created today — agent exits cleanly.")
            state.log_step("early_exit", StepStatus.SKIPPED,
                           {"reason": "no_materials_found"})
            return

        # ── Step 2 · Filter ───────────────────────────────────────────────────
        logging.info("━━━ Step 2 · Filter (idempotency) ━━━━━━━━━━━━━━━━━━━━━")
        if force:
            new_ids, skipped_ids = discovered, []
            logging.info("⚠️  --force : contrôle d'idempotence ignoré")
        else:
            new_ids, skipped_ids = state_manager.filter_new(discovered)
        state.new_ids     = new_ids
        state.skipped_ids = skipped_ids
        state.log_step("filter", StepStatus.SUCCESS, {
            "new_count":          len(new_ids),
            "already_done_count": len(skipped_ids),
        })

        if not new_ids:
            logging.info("All discovered IDs already processed today — agent exits cleanly.")
            state.log_step("early_exit", StepStatus.SKIPPED,
                           {"reason": "all_already_processed"})
            return

        # ── Step 3 · Write IDs file ───────────────────────────────────────────
        logging.info("━━━ Step 3 · Write IDs file ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        ids_file = _save_ids_file(new_ids, run_dir, run_date)
        state.log_step("write_ids_file", StepStatus.SUCCESS, {
            "file":  str(ids_file),
            "count": len(new_ids),
        })

        # ── Step 4 · Process batch ────────────────────────────────────────────
        logging.info("━━━ Step 4 · Process batch ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        # The CISPro session is only needed for the POST/PUT calls.
        if dry_run:
            logging.info("[DRY RUN] CISPro login skipped")
        else:
            connect_to_CISPro_api(username, password)
        h_codes_dict = read_tsv_file(TSV_PATH)

        for mid in new_ids:
            result = _process_one(
                mid, h_codes_dict, username, password,
                run_dir, use_llm, model_name, dry_run,
            )
            if result.status == StepStatus.SUCCESS:
                state.processed_ids.append(mid)
                # Persist immediately → crash-safe: only unprocessed IDs retry.
                # Never persisted in dry run: the ID was not sent to CISPro,
                # so the real run of the day must still pick it up.
                if not dry_run:
                    state_manager.mark_processed(mid)
            else:
                state.failed_ids.append(mid)

            state.log_step(f"process_{mid}", result.status, result.model_dump())

        if not dry_run:
            disconnect_from_CISPro_API()
        logging.info(
            f"Batch done: {len(state.processed_ids)} success  "
            f"/ {len(state.failed_ids)} failed"
        )
        state.log_step("process_batch_done", StepStatus.SUCCESS, {
            "ok":      len(state.processed_ids),
            "failed":  len(state.failed_ids),
            "dry_run": dry_run,
        })

        # ── Step 5 · Report ───────────────────────────────────────────────────
        logging.info("━━━ Step 5 · Report ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        state.end_time = datetime.now().isoformat()
        write_trace_log(state, run_dir)
        write_summary(state, run_dir)
        # State file untouched in dry run — including the housekeeping purge.
        if not dry_run:
            state_manager.purge_old_entries()

        logging.info(
            f"Run {run_id} completed." + (" (DRY RUN — nothing sent to CISPro)" if dry_run else "")
        )

    except EnvironmentError as exc:
        logging.error(f"Configuration error: {exc}")
        sys.exit(1)

    except Exception as exc:
        state.end_time = datetime.now().isoformat()
        state.log_step("critical_error", StepStatus.FAILED, {"error": str(exc)})
        logging.exception(f"Critical failure in run {run_id}")
        try:
            write_trace_log(state, run_dir)
            write_summary(state, run_dir)
        except Exception:
            pass
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SDS Parser Daily Agent — unattended nightly run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m agent.daily_agent
  python -m agent.daily_agent --llm
  python -m agent.daily_agent --sql queries/today_materials.sql --llm
  python -m agent.daily_agent --ids ids.txt --llm --dry-run --force
  python -m agent.daily_agent --ids ids.txt --euh-mode on --dry-run
        """,
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable LLM-based additional data extraction via Ollama",
    )
    parser.add_argument(
        "--sql",
        metavar="FILE",
        help="Path to a .sql file containing the discovery query (overrides default)",
    )
    parser.add_argument("--ids", metavar="FILE",
        help="Process IDs from a file (one Z-number per line) instead of DB discovery")
    parser.add_argument("--model", metavar="NAME",
        help="LLM model name (priority on LLM_MODEL_NAME of .env)")
    parser.add_argument("--force", action="store_true",
        help="Ignore idempotence (reprocess IDs already done today)")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
        help="Extract and save JSON locally, but never write to CISPro "
             "(agent_state.json is left untouched). Combine with --force to "
             "replay the same IDs as many times as needed.")
    parser.add_argument("--euh-mode", metavar="MODE", choices=VALID_EUH_MODES,
        help="EUH statement handling for this run: off (ignore) | audit (detect "
             "and log only) | on (add to labelCodes). Overrides EUH_MODE from "
             ".env. Pair with --dry-run to preview 'on' without touching CISPro.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    # Applied before run_agent so every material in the batch sees the same mode.
    # None leaves EUH_MODE from .env untouched.
    set_euh_mode(args.euh_mode)
    custom_sql = Path(args.sql).read_text(encoding="utf-8") if args.sql else None
    run_agent(
        sql_query=custom_sql,
        use_llm=args.llm,
        ids_file=args.ids,
        model_override=args.model,
        force=args.force,
        dry_run=args.dry_run,
    )
