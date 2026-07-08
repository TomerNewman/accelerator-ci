"""Phase-based progress reporting for long-running workflows."""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Tracks numbered steps through a multi-phase workflow.

    Human-readable output goes to the logger. When json_output is True,
    each step also emits a JSON line to stdout for CI tooling to parse.
    """

    def __init__(
        self,
        workflow: str,
        steps: list[str],
        json_output: bool = False,
    ) -> None:
        self.workflow = workflow
        self.steps = steps
        self.total = len(steps)
        self.json_output = json_output
        self._current = 0
        self._start_time = time.monotonic()
        self._step_start: float | None = None

    def _elapsed(self) -> float:
        return time.monotonic() - self._start_time

    def _emit_json(self, event: str, **fields) -> None:
        if not self.json_output:
            return
        record = {
            "event": event,
            "workflow": self.workflow,
            "elapsed_s": round(self._elapsed(), 1),
            **fields,
        }
        print(json.dumps(record), flush=True)

    def start(self) -> None:
        logger.info("[%s] Starting (%d steps)", self.workflow, self.total)
        self._emit_json("workflow_start", total_steps=self.total)

    def step(self, index: int) -> None:
        """Begin step `index` (1-based)."""
        if index < 1 or index > self.total:
            raise ValueError(f"step index {index} out of range [1, {self.total}]")

        if self._step_start is not None and self._current > 0:
            dur = time.monotonic() - self._step_start
            self._emit_json(
                "step_done",
                step=self._current,
                step_name=self.steps[self._current - 1],
                duration_s=round(dur, 1),
            )

        self._current = index
        self._step_start = time.monotonic()
        name = self.steps[index - 1]
        logger.info("[%s] Step %d/%d: %s", self.workflow, index, self.total, name)
        self._emit_json("step_start", step=index, step_name=name)

    def done(self) -> None:
        if self._step_start is not None and self._current > 0:
            dur = time.monotonic() - self._step_start
            self._emit_json(
                "step_done",
                step=self._current,
                step_name=self.steps[self._current - 1],
                duration_s=round(dur, 1),
            )

        total_dur = self._elapsed()
        logger.info("[%s] Completed (%d steps in %.0fs)", self.workflow, self.total, total_dur)
        self._emit_json("workflow_done", total_duration_s=round(total_dur, 1))

    def fail(self, error: str) -> None:
        total_dur = self._elapsed()
        step_name = self.steps[self._current - 1] if self._current > 0 else ""
        logger.error(
            "[%s] Failed at step %d/%d (%s) after %.0fs: %s",
            self.workflow, self._current, self.total, step_name or "not started", total_dur, error,
        )
        self._emit_json(
            "workflow_failed",
            step=self._current,
            step_name=step_name,
            error=error,
            total_duration_s=round(total_dur, 1),
        )
