"""The handle a running job uses to report progress and record checkpoints.

A task needs three things from the machinery running it: what it was asked to do,
what a previous attempt already finished, and somewhere to record what it has
finished now. ``JobContext`` is those three things and nothing else, so a task can
be executed by the durable runner, by a test, or straight from the CLI without
changing.

Checkpoints are written in their own transaction, deliberately. A checkpoint that
lives inside the task's transaction disappears exactly when the task fails, which
is the one moment it was needed. Writing it separately means the record of "this
stage finished" survives the failure of the stage after it.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Key under which completed stage names are recorded inside a job's checkpoint.
STAGES_KEY = "completed_stages"

CheckpointWriter = Callable[[dict[str, Any], float], Awaitable[None]]


class JobCancelledError(Exception):
    """Raised when a job has been cancelled by an operator during execution (§24)."""


@dataclass
class JobContext:
    """What a background task is given while it runs."""

    job_id: str
    job_type: str
    project_id: str
    #: Everything the submitter asked for. Named ``parameters`` in the database;
    #: exposed here under the name the existing tasks already use.
    metadata: dict[str, Any] = field(default_factory=dict)
    #: The checkpoint left by a previous attempt. Empty on a first attempt.
    resumed_from: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1
    progress: float = 0.0
    #: Set by the durable runner. Absent when a task is run directly, in which
    #: case checkpoints are kept in memory and simply have no effect on recovery.
    writer: CheckpointWriter | None = None
    cancel_checker: Callable[[], Awaitable[bool]] | None = None

    def __post_init__(self) -> None:
        self._state: dict[str, Any] = dict(self.resumed_from or {})
        self._state.setdefault(STAGES_KEY, list(self._state.get(STAGES_KEY, [])))

    @property
    def resumed(self) -> bool:
        """Whether this attempt is continuing work a previous attempt started."""
        return bool(self.resumed_from)

    def already_done(self, stage: str) -> bool:
        """Whether a previous attempt recorded this stage as finished.

        A task uses this to skip work it has already applied. Skipping is only
        safe for stages that were themselves committed before the checkpoint was
        written, which is why ``checkpoint`` is called *after* the commit and not
        before it.
        """
        return stage in (self.resumed_from.get(STAGES_KEY) or [])

    def value(self, key: str, default: Any = None) -> Any:
        """A value a previous attempt recorded, for resuming mid-stage."""
        return (self.resumed_from or {}).get(key, default)

    async def check_cancelled(self) -> None:
        """Check if job was cancelled and immediately abort execution if so (§24)."""
        if self.cancel_checker is not None and await self.cancel_checker():
            raise JobCancelledError(f"Job {self.job_id} was cancelled.")

    async def checkpoint(
        self, stage: str, progress: float | None = None, **detail: Any
    ) -> None:
        """Record that a stage finished, and how far along the job now is.

        Called after the stage's own work is committed. If the process dies
        between the commit and this call the stage simply runs again, which is
        the safe direction to fail in: repeating idempotent work costs time,
        whereas skipping work that was never committed loses it.
        """
        await self.check_cancelled()
        stages = self._state.setdefault(STAGES_KEY, [])
        if stage not in stages:
            stages.append(stage)
        if detail:
            self._state.update(detail)
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))

        if self.writer is None:
            return
        try:
            await self.writer(dict(self._state), self.progress)
        except Exception:
            # A checkpoint that cannot be written is a recovery problem, not a
            # reason to abandon work that is otherwise succeeding.
            logger.warning(
                "Could not persist checkpoint for job %s at stage '%s'; the job "
                "continues, but a crash from here would restart this stage.",
                self.job_id,
                stage,
                exc_info=True,
            )

    @property
    def state(self) -> dict[str, Any]:
        """The checkpoint as it currently stands."""
        return dict(self._state)
