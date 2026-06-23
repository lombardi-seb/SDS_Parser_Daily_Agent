# agent/state_manager.py
# Step 2 of the daily agent pipeline:
# Tracks which Z-numbers have already been successfully processed,
# keyed by calendar date, so the agent is safe to restart after a crash.
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

_DEFAULT_STATE_FILE = Path("agent_state.json")


class StateManager:
    """
    Idempotency guard — persists processed Z-numbers by date.

    Storage format (agent_state.json):
    {
        "2026-06-18": ["Z0132456", "Z0132457"],
        "2026-06-17": ["Z0131000"]
    }

    Design principle:
        mark_processed() writes to disk immediately after each successful
        material, so a mid-run crash only leaves the remaining IDs to retry
        on the next run — no double-processing of already-succeeded materials.
    """

    def __init__(self, state_file: Path = _DEFAULT_STATE_FILE) -> None:
        self.state_file = state_file
        self._state: dict[str, list[str]] = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, list[str]]:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                logging.warning("⚠️  State file unreadable — starting fresh.")
        return {}

    def _save(self) -> None:
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2, sort_keys=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_processed_today(self) -> set[str]:
        """Return the set of IDs already successfully processed today."""
        return set(self._state.get(str(date.today()), []))

    def filter_new(self, ids: list[str]) -> tuple[list[str], list[str]]:
        """
        Split *ids* into (new_ids, already_done_ids).

        Returns:
            new_ids:       IDs to process in this run.
            already_done:  IDs skipped — they were processed earlier today.
        """
        done         = self.get_processed_today()
        new          = [i for i in ids if i not in done]
        already_done = [i for i in ids if i in done]
        if already_done:
            logging.info(
                f"⏭️  Skipping {len(already_done)} already-processed ID(s): "
                f"{already_done}"
            )
        return new, already_done

    def mark_processed(self, material_id: str) -> None:
        """
        Mark *material_id* as successfully processed today.
        Persists immediately — crash-safe at per-ID granularity.
        """
        today  = str(date.today())
        bucket = self._state.setdefault(today, [])
        if material_id not in bucket:
            bucket.append(material_id)
        self._save()

    def purge_old_entries(self, keep_days: int = 30) -> None:
        """Remove entries older than *keep_days* to keep the file compact."""
        cutoff   = str(date.today() - timedelta(days=keep_days))
        old_keys = [k for k in self._state if k < cutoff]
        for k in old_keys:
            del self._state[k]
        if old_keys:
            logging.info(f"🧹 Purged {len(old_keys)} old state entr(ies).")
            self._save()
