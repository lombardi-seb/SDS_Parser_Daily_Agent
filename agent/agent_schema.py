# agent/agent_schema.py
# Pydantic models that describe the state of a single agent run.
# Inspired by UdaciScan's schema.py / RepurposingBrief pattern.
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    WARNING = "warning"


class MaterialResult(BaseModel):
    """
    Outcome of processing a single Z-number through the SDS pipeline.

    The two *_posted fields are tri-state on purpose:
        True  → CISPro accepted the payload
        False → CISPro call was made and failed
        None  → call was never attempted (dry run, or earlier step failed)
    """
    material_id:            str
    status:                 StepStatus = StepStatus.FAILED
    dry_run:                bool = False
    main_json_saved:        bool = False
    main_json_posted:       Optional[bool] = None
    additional_json_saved:  bool = False
    additional_json_posted: Optional[bool] = None
    error:                  Optional[str] = None
    duration_s:             float = 0.0


class AgentStep(BaseModel):
    """One recorded step in the agent trajectory (written to trace JSONL)."""
    step:      str
    status:    StepStatus
    timestamp: str  = Field(default_factory=lambda: datetime.now().isoformat())
    details:   dict = Field(default_factory=dict)


class AgentRunState(BaseModel):
    """
    Full mutable state of one agent run.
    Passed between steps and serialised at the end by reporter.py.
    """
    run_id:         str
    run_date:       str                  # ISO date, e.g. "2026-06-18"
    discovered_ids: list[str]            = Field(default_factory=list)
    new_ids:        list[str]            = Field(default_factory=list)
    processed_ids:  list[str]            = Field(default_factory=list)
    failed_ids:     list[str]            = Field(default_factory=list)
    skipped_ids:    list[str]            = Field(default_factory=list)
    steps:          list[AgentStep]      = Field(default_factory=list)
    start_time:     Optional[str]        = None
    end_time:       Optional[str]        = None
    use_llm:        bool                 = False
    model_name:     Optional[str]        = None
    # True when the run performed extraction only: no CISPro write,
    # no persistence in agent_state.json.
    dry_run:        bool                 = False

    def log_step(
        self,
        step_name: str,
        status: StepStatus,
        details: dict | None = None,
    ) -> None:
        """Append a step record to the trajectory (thread-safe for single-threaded use)."""
        self.steps.append(
            AgentStep(step=step_name, status=status, details=details or {})
        )
