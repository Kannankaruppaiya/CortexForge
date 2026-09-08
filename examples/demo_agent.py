"""CortexForge Agent Interaction Demo.

Demonstrates how an AI coding agent recovers project understanding, verifies active
architectural constraints, assesses blast radius before editing code, and logs durable
lessons without re-reading the entire repository.
"""

import asyncio
import os

from rich.console import Console
from rich.panel import Panel

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.graph.service import GraphService
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.service import MemoryService
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine

console = Console()


async def main() -> None:
    console.print(Panel(
        "[bold cyan]CortexForge: AI Coding Agent Memory Layer Demonstration[/]\n"
        "Demonstrating the continuous loop: OBSERVE -> RETRIEVE -> ACT -> VERIFY -> STORE",
        border_style="cyan",
    ))

    # 1. Initialize DB and Services
    await init_db()
    graph_service = GraphService()
    memory_service = MemoryService()
    verification_engine = MemoryVerificationEngine()
    consolidation_engine = MemoryConsolidationEngine(memory_service=memory_service)
    retrieval_engine = HybridRetrievalEngine(graph_service=graph_service)
    composer = ContextComposer(retrieval_engine=retrieval_engine, graph_service=graph_service)
    change_propagator = SemanticChangePropagator(graph_service=graph_service)

    repo_path = os.path.realpath(".")

    async with session_scope() as session:
        # 2. Get or Register Project
        from sqlalchemy import select
        res = await session.execute(select(Project).where(Project.local_path == repo_path))
        project = res.scalars().first()

        if not project:
            project = Project(
                name="CortexForgeDemo",
                local_path=repo_path,
                status="ACTIVE",
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)

        console.print(f"\n[bold green]Step 1:[/] Connected to project '[bold white]{project.name}[/]' (ID: {project.id[:8]}...)")

        # 3. Retrieve Token-Budget Structured Context for Agent Task
        task_prompt = "Refactor database engine to support connection pool timeouts without breaking SQLite in-memory tests."
        console.print(f"\n[bold green]Step 2:[/] Requesting structured prompt context for agent task:\n  [yellow]\"{task_prompt}\"[/]")

        context_md = await composer.build_context(
            session,
            project_id=project.id,
            task_text=task_prompt,
            profile="small",
            target_files=["src/cortexforge/core/db.py"],
        )

        console.print(Panel(context_md, title="Agent Context Packet (L0 - L5)", border_style="green"))

        # 4. Pre-Action Check: Blast Radius & Constraint Violations
        target_files = ["src/cortexforge/core/db.py", "src/cortexforge/core/models.py"]
        console.print(f"\n[bold green]Step 3:[/] Pre-Action Check: Simulating prospective edit blast radius on {target_files}...")

        impact = await change_propagator.propagate_changes(
            session,
            project_id=project.id,
            modified_files=target_files,
            mark_stale=False,
        )

        console.print(f"  - Directly Changed Entities: [bold white]{len(impact.directly_changed_entities)}[/] symbols")
        console.print(f"  - Callers in Blast Radius:   [bold yellow]{len(impact.affected_dependents)}[/] consumers")
        console.print(f"  - Flagged Stale Memories:    [bold red]{len(impact.memories_flagged_stale)}[/] memories")

        if impact.critical_constraints:
            console.print("  - [bold red]CRITICAL CONSTRAINTS AT RISK:[/]")
            for c in impact.critical_constraints:
                console.print(f"    [red]• {c}[/]")

        # 5. Store Completed Work Knowledge (Decision + Failure Post-Mortem)
        console.print("\n[bold green]Step 4:[/] Recording verified architectural decision into cognitive layer...")
        created_mem = await memory_service.create_memory(
            session,
            project.id,
            MemoryCreate(
                memory_type="DECISION",
                title="Strict Async Context Session Scoping",
                content="Always use async with session_scope() context manager to ensure automatic rollback on failure and clean disposal.",
                summary="Async session scope context manager invariant",
                importance=0.9,
                evidence=[
                    MemoryEvidenceCreate(source_type="file", file_path="src/cortexforge/core/db.py", confidence=1.0)
                ],
            ),
        )
        console.print(f"  Stored memory [cyan]{created_mem.id}[/] ([white]{created_mem.title}[/])")

        # 6. Verify Grounding Against Filesystem
        console.print("\n[bold green]Step 5:[/] Verifying memory grounding against active working tree...")
        v_report = await verification_engine.verify_project_memories(session, project.id)
        console.print(f"  Verification Result: [bold green]{v_report.get('verified', 0)} Active[/], [yellow]{v_report.get('stale', 0)} Stale[/], [red]{v_report.get('deprecated', 0)} Deprecated[/]")



        # 7. Consolidation Loop
        console.print("\n[bold green]Step 6:[/] Running memory consolidation to synthesize durable lessons...")
        c_report = await consolidation_engine.consolidate_project(session, project.id)
        console.print(f"  Consolidation Result: Merged [bold purple]{c_report.get('clustered_count', 0)}[/] episodes into [bold green]{c_report.get('lessons_created', 0)}[/] durable lessons.")

    console.print("\n[bold cyan]Demonstration Completed Successfully![/] CortexForge maintained verified project memory with zero full-repo re-reading.\n")


if __name__ == "__main__":
    asyncio.run(main())
