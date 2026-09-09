"""Executable background task routines for CortexForge using JobContext."""

from typing import Any

from sqlalchemy import delete

from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import session_scope
from cortexforge.core.models import CodeEntity, Project, Relationship
from cortexforge.evaluation.runner import EvaluationRunner
from cortexforge.graph.service import GraphService
from cortexforge.jobs.context import JobContext
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.service import MemoryService
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine


async def rebuild_project_task(job: JobContext) -> dict[str, Any]:
    """Perform a complete, clean rebuild and recovery of a project's cognitive model.

    Supports checkpoint resumption across discrete stages:
    1. purge: Clears existing entities and relationships.
    2. rescan: Full AST parsing of the repository.
    3. verify_memories: Re-verification of all durable memories.
    4. validate_architecture: Re-computing architectural graph modules.
    """
    project_id = job.project_id
    scanner = RepositoryScanner()
    verification_engine = MemoryVerificationEngine()
    graph_service = GraphService()

    scan_data = {
        "files_scanned": job.value("files_scanned", 0),
        "entities_extracted": job.value("entities_extracted", 0),
        "relationships_extracted": job.value("relationships_extracted", 0),
    }
    verif_data = {
        "verified": job.value("verified", 0),
        "stale": job.value("stale", 0),
    }

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        # Stage 1: Purge existing code entities and relationships
        if not job.already_done("purge"):
            await session.execute(
                delete(Relationship).where(Relationship.project_id == project_id)
            )
            await session.execute(
                delete(CodeEntity).where(CodeEntity.project_id == project_id)
            )
            await session.commit()
            await job.checkpoint("purge", progress=0.2)

        # Stage 2: Full AST rescan
        if not job.already_done("rescan"):
            scan_res = await scanner.scan_project(session, project, incremental=False)
            scan_data["files_scanned"] = scan_res.files_scanned
            scan_data["entities_extracted"] = scan_res.entities_extracted
            scan_data["relationships_extracted"] = scan_res.relationships_extracted
            await job.checkpoint(
                "rescan",
                progress=0.6,
                files_scanned=scan_res.files_scanned,
                entities_extracted=scan_res.entities_extracted,
                relationships_extracted=scan_res.relationships_extracted,
            )

        # Stage 3: Re-verify durable memories
        if not job.already_done("verify_memories"):
            verif_counts = await verification_engine.verify_project_memories(
                session, project_id
            )
            verif_data["verified"] = verif_counts["verified"]
            verif_data["stale"] = verif_counts["stale"]
            await job.checkpoint(
                "verify_memories",
                progress=0.85,
                verified=verif_counts["verified"],
                stale=verif_counts["stale"],
            )

        # Stage 4: Validate architectural graph consistency
        arch = await graph_service.get_project_architecture(
            session, project_id, depth=2
        )
        project.status = "READY"
        await session.commit()
        await job.checkpoint("validate_architecture", progress=1.0)

        return {
            "status": "REBUILT",
            "files_scanned": scan_data["files_scanned"],
            "entities_extracted": scan_data["entities_extracted"],
            "relationships_extracted": scan_data["relationships_extracted"],
            "memories_verified": verif_data["verified"],
            "memories_stale": verif_data["stale"],
            "modules_mapped": len(arch.modules) if arch else 0,
        }


async def scan_project_task(job: JobContext) -> dict[str, Any]:
    """Background repository AST scan with checkpointing."""
    project_id = job.project_id
    scanner = RepositoryScanner()
    incremental = job.metadata.get("incremental", True)
    max_files = job.metadata.get("max_files")

    await job.checkpoint("starting", progress=0.1)

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        scan_res = await scanner.scan_project(
            session, project, incremental=incremental, max_files=max_files
        )

        await job.checkpoint("completed", progress=1.0)

        return {
            "files_scanned": scan_res.files_scanned,
            "entities_extracted": scan_res.entities_extracted,
            "relationships_extracted": scan_res.relationships_extracted,
            "duration_ms": scan_res.duration_ms,
            "status": scan_res.status,
        }


async def consolidate_project_task(job: JobContext) -> dict[str, Any]:
    """Background memory consolidation."""
    project_id = job.project_id
    memory_service = MemoryService()
    consolidation_engine = MemoryConsolidationEngine(memory_service=memory_service)

    await job.checkpoint("consolidating", progress=0.2)

    async with session_scope() as session:
        result = await consolidation_engine.consolidate_project(session, project_id)
        await job.checkpoint("completed", progress=1.0)
        return result


async def benchmark_project_task(job: JobContext) -> dict[str, Any]:
    """Background empirical evaluation benchmark run."""
    project_id = job.project_id
    graph_service = GraphService()
    retrieval_engine = HybridRetrievalEngine(graph_service=graph_service)
    context_composer = ContextComposer(
        retrieval_engine=retrieval_engine, graph_service=graph_service
    )
    scanner = RepositoryScanner()
    runner = EvaluationRunner(
        retrieval_engine=retrieval_engine,
        context_composer=context_composer,
        scanner=scanner,
    )

    await job.checkpoint("benchmarking", progress=0.2)

    async with session_scope() as session:
        scorecards = await runner.run_benchmark(session, project_id)
        await job.checkpoint("completed", progress=1.0)
        return {
            "scorecards_count": len(scorecards),
            "results": [sc.__dict__ for sc in scorecards],
        }
