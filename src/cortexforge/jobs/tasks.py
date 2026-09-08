"""Executable background task routines for CortexForge."""

from typing import Any

from sqlalchemy import delete

from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import session_scope
from cortexforge.core.models import CodeEntity, Project, Relationship
from cortexforge.evaluation.runner import EvaluationRunner
from cortexforge.graph.service import GraphService
from cortexforge.jobs.manager import JobRecord
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.service import MemoryService
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine


async def rebuild_project_task(job: JobRecord) -> dict[str, Any]:
    """Perform a complete, clean rebuild and recovery of a project's cognitive model."""
    project_id = job.project_id
    scanner = RepositoryScanner()
    verification_engine = MemoryVerificationEngine()
    graph_service = GraphService()

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        job.progress = 0.1
        # Step 1: Purge existing code entities and relationships to ensure clean slate
        await session.execute(delete(Relationship).where(Relationship.project_id == project_id))
        await session.execute(delete(CodeEntity).where(CodeEntity.project_id == project_id))
        await session.commit()

        job.progress = 0.3
        # Step 2: Full AST rescan
        scan_res = await scanner.scan_project(session, project, incremental=False)

        job.progress = 0.7
        # Step 3: Re-verify all existing durable memories against newly parsed AST symbols
        verif_counts = await verification_engine.verify_project_memories(session, project_id)

        job.progress = 0.9
        # Step 4: Validate architectural graph consistency
        arch = await graph_service.get_project_architecture(session, project_id, depth=2)

        # Update project status
        project.status = "READY"
        await session.commit()

        job.progress = 1.0
        return {
            "status": "REBUILT",
            "files_scanned": scan_res.files_scanned,
            "entities_extracted": scan_res.entities_extracted,
            "relationships_extracted": scan_res.relationships_extracted,
            "memories_verified": verif_counts["verified"],
            "memories_stale": verif_counts["stale"],
            "modules_mapped": len(arch.modules) if arch else 0,
        }


async def scan_project_task(job: JobRecord) -> dict[str, Any]:
    """Background repository AST scan."""
    project_id = job.project_id
    scanner = RepositoryScanner()
    incremental = job.metadata.get("incremental", True)
    max_files = job.metadata.get("max_files")

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        scan_res = await scanner.scan_project(session, project, incremental=incremental, max_files=max_files)
        return {
            "files_scanned": scan_res.files_scanned,
            "entities_extracted": scan_res.entities_extracted,
            "relationships_extracted": scan_res.relationships_extracted,
            "duration_ms": scan_res.duration_ms,
            "status": scan_res.status,
        }


async def consolidate_project_task(job: JobRecord) -> dict[str, Any]:
    """Background memory consolidation."""
    project_id = job.project_id
    memory_service = MemoryService()
    consolidation_engine = MemoryConsolidationEngine(memory_service=memory_service)

    async with session_scope() as session:
        return await consolidation_engine.consolidate_project(session, project_id)


async def benchmark_project_task(job: JobRecord) -> dict[str, Any]:
    """Background empirical evaluation benchmark run."""
    project_id = job.project_id
    graph_service = GraphService()
    retrieval_engine = HybridRetrievalEngine(graph_service=graph_service)
    context_composer = ContextComposer(retrieval_engine=retrieval_engine, graph_service=graph_service)
    scanner = RepositoryScanner()
    runner = EvaluationRunner(
        retrieval_engine=retrieval_engine,
        context_composer=context_composer,
        scanner=scanner,
    )

    async with session_scope() as session:
        scorecards = await runner.run_benchmark(session, project_id)
        return {
            "scorecards_count": len(scorecards),
            "results": [sc.__dict__ for sc in scorecards],
        }
