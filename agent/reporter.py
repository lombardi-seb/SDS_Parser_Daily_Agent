# agent/reporter.py
# Step 5 of the daily agent pipeline:
# Writes the run artefacts to disk — trajectory log + human-readable summary.
from __future__ import annotations

import json
import logging
from pathlib import Path

from agent.agent_schema import AgentRunState


def write_trace_log(run_state: AgentRunState, runs_dir: Path) -> Path:
    """
    Write a JSONL trajectory log — one JSON object per agent step.

    Each line is a self-contained AgentStep record (step name, status,
    timestamp, details). Useful for debugging and auditing.

    Output: runs/trace_<run_id>.jsonl
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    log_file = runs_dir / f"trace_{run_state.run_id}.jsonl"

    with open(log_file, "w", encoding="utf-8") as f:
        for step in run_state.steps:
            f.write(step.model_dump_json() + "\n")

    logging.info(f"📋 Trajectory log  → {log_file}")
    return log_file


def write_summary(run_state: AgentRunState, runs_dir: Path) -> Path:
    """
    Write a JSON summary of the agent run.

    Output: runs/summary_<run_id>.json
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    summary_file = runs_dir / f"summary_{run_state.run_id}.json"

    summary = {
        "run_id":    run_state.run_id,
        "run_date":  run_state.run_date,
        "start_time": run_state.start_time,
        "end_time":  run_state.end_time,
        "statistics": {
            "discovered":           len(run_state.discovered_ids),
            "new_to_process":       len(run_state.new_ids),
            "processed_ok":         len(run_state.processed_ids),
            "failed":               len(run_state.failed_ids),
            "skipped_already_done": len(run_state.skipped_ids),
        },
        "failed_ids":  run_state.failed_ids,
        "llm_enabled": run_state.use_llm,
        "model":       run_state.model_name,
    }

    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logging.info(f"📊 Summary         → {summary_file}")
    _print_summary_to_log(summary)
    return summary_file


def _print_summary_to_log(summary: dict) -> None:
    s   = summary["statistics"]
    sep = "=" * 52
    logging.info(sep)
    logging.info("  RUN SUMMARY")
    logging.info(f"  Date            : {summary['run_date']}")
    logging.info(f"  Discovered      : {s['discovered']}")
    logging.info(f"  New IDs         : {s['new_to_process']}")
    logging.info(f"  Success         : {s['processed_ok']}")
    logging.info(f"  Failed          : {s['failed']}")
    logging.info(f"  Skipped         : {s['skipped_already_done']}")
    if summary["failed_ids"]:
        logging.warning(f"  Failed IDs      : {summary['failed_ids']}")
    logging.info(sep)
