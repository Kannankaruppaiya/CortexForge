"""Standalone background worker process for durable CortexForge jobs (§30, §38).

Executes background work independently from the API server:
- Continuous job polling and claiming with leases and heartbeats.
- Executes repository scans, memory consolidation, graph rebuilds, and benchmarks.
- Cleanly shuts down on SIGINT/SIGTERM without abandoning claimed jobs.
"""

import asyncio
import logging
import signal
import sys
from typing import Any

from cortexforge.core.db import init_db
from cortexforge.jobs.durable import DurableJobStore
from cortexforge.jobs.runner import JobRunner
from cortexforge.jobs.tasks import (
    benchmark_project_task,
    consolidate_project_task,
    import_and_scan_project_task,
    rebuild_project_task,
    scan_project_task,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [cortex-worker] %(name)s: %(message)s",
)
logger = logging.getLogger("cortexforge.jobs.worker")


def create_configured_runner(store: DurableJobStore | None = None) -> JobRunner:
    """Instantiate a JobRunner with all task handlers registered."""
    job_store = store or DurableJobStore()
    runner = JobRunner(store=job_store)
    runner.register("REBUILD", rebuild_project_task)
    runner.register("SCAN", scan_project_task)
    runner.register("IMPORT_AND_SCAN", import_and_scan_project_task)
    runner.register("CONSOLIDATE", consolidate_project_task)
    runner.register("BENCHMARK", benchmark_project_task)
    return runner


async def run_worker_loop(
    runner: JobRunner | None = None,
    poll_interval_seconds: float = 1.0,
    stop_event: asyncio.Event | None = None,
    init_database: bool = True,
) -> None:
    """Initialize DB and run the continuous job execution loop until stopped."""
    if init_database:
        logger.info("Initializing database connection for worker runtime...")
        await init_db()

    actual_runner = runner or create_configured_runner()
    actual_stop_event = stop_event or asyncio.Event()

    logger.info(
        "Worker process started successfully (worker_id=%s). Polling for queued jobs...",
        actual_runner.worker_id,
    )

    await actual_runner.run_loop(
        poll_interval_seconds=poll_interval_seconds,
        stop_event=actual_stop_event,
    )

    logger.info("Worker process completed shutdown cleanly.")


def main() -> None:
    """CLI entrypoint for running the CortexForge durable background worker."""
    stop_event = asyncio.Event()

    def handle_exit(signum: Any, frame: Any) -> None:
        logger.info(
            "Received termination signal (%s), requesting graceful stop...", signum
        )
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_exit)
        except Exception as exc:
            logger.warning("Could not register signal handler for %s: %s", sig, exc)

    try:
        asyncio.run(run_worker_loop(stop_event=stop_event))
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received; exiting worker.")
        sys.exit(0)


if __name__ == "__main__":
    main()
