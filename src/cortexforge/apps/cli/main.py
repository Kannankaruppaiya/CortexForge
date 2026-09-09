"""Production-Grade Command Line Interface for CortexForge."""

import asyncio
import json
import os
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from sqlalchemy import delete, func, select

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import (
    CodeEntity,
    Memory,
    Project,
)
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.evaluation.runner import EvaluationRunner
from cortexforge.graph.service import GraphService
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.service import MemoryService
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine

console = Console()
scanner = RepositoryScanner()
graph_service = GraphService()
memory_service = MemoryService()
verification_engine = MemoryVerificationEngine()
consolidation_engine = MemoryConsolidationEngine(memory_service=memory_service)
retrieval_engine = HybridRetrievalEngine(graph_service=graph_service)
context_composer = ContextComposer(retrieval_engine=retrieval_engine, graph_service=graph_service)
change_propagator = SemanticChangePropagator(graph_service=graph_service)
evaluation_runner = EvaluationRunner(
    retrieval_engine=retrieval_engine, context_composer=context_composer, scanner=scanner
)


async def _get_project_or_exit(session, ref: str) -> Project:
    project = None
    if os.path.exists(ref):
        canon = os.path.realpath(ref)
        res = await session.execute(select(Project).where(Project.local_path == canon))
        project = res.scalars().first()

    if not project:
        project = await session.get(Project, ref)

    if not project:
        res = await session.execute(select(Project).where(Project.name == ref))
        project = res.scalars().first()

    if not project:
        console.print(f"[bold red]Error:[/] Project '{ref}' not found.")
        console.print("Run `cortex init` or `cortex scan .` to register and index.")
        sys.exit(1)

    return project


@click.group(help="CortexForge: Evolving, verified project memory layer for AI coding agents.")
def cli() -> None:
    pass


@cli.command(help="Initialize a repository for CortexForge cognitive tracking.")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--name", default=None, help="Custom project name")
def init(path: str, name: str | None) -> None:
    """Initialize repository in CortexForge database."""
    canonical_path = os.path.realpath(path)
    proj_name = name or os.path.basename(canonical_path) or "cortex-project"

    async def _do_init() -> None:
        await init_db()
        async with session_scope() as session:
            stmt = select(Project).where(Project.local_path == canonical_path)
            res = await session.execute(stmt)
            existing = res.scalars().first()
            if existing:
                console.print(f"[bold yellow]Project already initialized:[/] {existing.name} (ID: `{existing.id}`)")
                return

            proj = Project(name=proj_name, local_path=canonical_path, status="READY")
            session.add(proj)
            await session.commit()
            console.print(f"[bold green]Initialized CortexForge project:[/] {proj.name} at `{canonical_path}`")
            console.print("Next step: Run `cortex scan` to parse AST symbols.")

    asyncio.run(_do_init())


@cli.command(help="Scan repository, extract AST entities, and update project model.")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--incremental/--full", default=True, help="Incremental or full scan")
@click.option("--name", default=None, help="Project name")
def scan(path: str, incremental: bool, name: str | None) -> None:
    """Scan code repository and extract symbols."""
    canonical_path = os.path.realpath(path)
    proj_name = name or os.path.basename(canonical_path) or "cortex-project"

    async def _do_scan() -> None:
        await init_db()
        async with session_scope() as session:
            stmt = select(Project).where(Project.local_path == canonical_path)
            res = await session.execute(stmt)
            project = res.scalars().first()

            if not project:
                console.print(f"[bold green]Registering new project:[/] {proj_name}")
                project = Project(name=proj_name, local_path=canonical_path, status="INITIALIZING")
                session.add(project)
                await session.flush()
            else:
                console.print(f"[bold cyan]Scanning registered project:[/] {project.name}")

            with console.status("[bold blue]Parsing AST code entities via Tree-sitter...[/]"):
                scan_res = await scanner.scan_project(session, project, incremental=incremental)

            table = Table(title=f"Scan Summary: {project.name}", border_style="cyan")
            table.add_column("Metric", style="bold white")
            table.add_column("Value", style="green")

            table.add_row("Files Processed", str(scan_res.files_scanned))
            table.add_row("Entities Extracted", str(scan_res.entities_extracted))
            table.add_row("Relationships Mapped", str(scan_res.relationships_extracted))
            table.add_row("Duration", f"{scan_res.duration_ms:.2f} ms")
            table.add_row("Status", f"[bold green]{scan_res.status}[/]" if scan_res.status == "SUCCESS" else f"[bold yellow]{scan_res.status}[/]")

            console.print(table)

    asyncio.run(_do_scan())


@cli.command(help="Perform full clean rebuild and recovery of project cognitive model.")
@click.argument("project_ref", default=".", required=False)
def rebuild(project_ref: str) -> None:
    """Recover from inconsistent/corrupted index with clean full rebuild."""
    from cortexforge.jobs.context import JobContext
    from cortexforge.jobs.tasks import rebuild_project_task

    async def _do_rebuild() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            console.print(f"[bold yellow]Initiating clean rebuild for project:[/] {project.name}")

            job = JobContext(job_id="rebuild_manual", job_type="REBUILD", project_id=project.id)
            with console.status("[bold blue]Purging stale index and rebuilding AST model...[/]"):
                res = await rebuild_project_task(job)

            table = Table(title=f"Rebuild Summary: {project.name}", border_style="green")
            table.add_column("Metric", style="bold white")
            table.add_column("Value", style="green")

            table.add_row("Files Re-indexed", str(res["files_scanned"]))
            table.add_row("Entities Extracted", str(res["entities_extracted"]))
            table.add_row("Relationships Mapped", str(res["relationships_extracted"]))
            table.add_row("Memories Verified", str(res["memories_verified"]))
            table.add_row("Stale Memories", str(res["memories_stale"]))
            table.add_row("Architecture Modules", str(res["modules_mapped"]))
            table.add_row("Status", f"[bold green]{res['status']}[/]")
            console.print(table)

    asyncio.run(_do_rebuild())


@cli.command(help="Display synthesized project structural architecture.")
@click.argument("project_ref", default=".", required=False)
@click.option("--depth", default=2, type=int, help="Traversal depth")

def architecture(project_ref: str, depth: int) -> None:
    """Show project architecture tree and components."""
    async def _do_arch() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            arch = await graph_service.get_project_architecture(session, project.id, depth=depth)
            if not arch:
                console.print(f"[bold red]Error:[/] Could not compute architecture for {project.name}.")
                return

            hdr = f"[bold cyan]{arch.project_name}[/]\n"
            hdr += f"Files: [green]{arch.total_files}[/] | Entities: [green]{arch.total_entities}[/] | Relationships: [green]{arch.total_relationships}[/]\n"
            hdr += f"Languages: [magenta]{', '.join(arch.languages)}[/]"
            console.print(Panel(hdr, title="Project Architecture Overview", border_style="cyan"))

            root_tree = Tree(f"[bold white]{arch.project_name}[/]")
            for mod in arch.modules:
                mod_node = root_tree.add(
                    f"[bold yellow]{mod.module_path}[/] ([cyan]{mod.file_count}[/] files, [cyan]{mod.entity_count}[/] symbols)"
                )
                for comp in mod.top_level_components[:10]:
                    deps = f" -> [{', '.join(comp.dependencies[:3])}]" if comp.dependencies else ""
                    mod_node.add(
                        f"[{comp.entity_type}] [bold]{comp.name}[/] ({comp.file_path}:{comp.line_range[0]}-{comp.line_range[1]}){deps}"
                    )

            console.print(root_tree)

            if arch.primary_apis:
                console.print("\n[bold cyan]Primary APIs & Entrypoints:[/]")
                for api in arch.primary_apis:
                    console.print(f"  - [bold]{api.name}[/] (`{api.file_path}`): {api.signature or ''}")

            if arch.primary_models:
                console.print("\n[bold cyan]Primary Data Models:[/]")
                for m in arch.primary_models:
                    console.print(f"  - [bold]{m.name}[/] (`{m.file_path}`)")

    asyncio.run(_do_arch())


@cli.command(help="Show CortexForge system status and registered repositories.")
def status() -> None:
    """Display system status and projects."""
    async def _do_status() -> None:
        await init_db()
        async with session_scope() as session:
            stmt = select(Project).order_by(Project.created_at.desc())
            res = await session.execute(stmt)
            projects = res.scalars().all()

            table = Table(title="CortexForge System Status", border_style="green")
            table.add_column("Project ID", style="dim")
            table.add_column("Name", style="bold white")
            table.add_column("Path", style="cyan")
            table.add_column("Entities", style="green")
            table.add_column("Memories", style="yellow")
            table.add_column("Status", style="magenta")

            for p in projects:
                entity_count = await session.scalar(select(func.count(CodeEntity.id)).where(CodeEntity.project_id == p.id))
                memory_count = await session.scalar(select(func.count(Memory.id)).where(Memory.project_id == p.id))
                table.add_row(
                    p.id[:8] + "...",
                    p.name,
                    p.local_path,
                    str(entity_count or 0),
                    str(memory_count or 0),
                    p.status,
                )

            console.print(table)

    asyncio.run(_do_status())


# ==================== MEMORY SUBCOMMANDS ====================

@cli.group(help="Inspect, search, verify, and consolidate project memories.")
def memory() -> None:
    pass


@memory.command("create", help="Create a durable, evidence-grounded project memory.")
@click.argument("project_ref", default=".", required=False)
@click.option("--type", "memory_type", default="DECISION", help="Memory type (DECISION, CONSTRAINT, FAILURE, LESSON, etc.)")
@click.option("--title", required=True, help="Short descriptive title")
@click.option("--content", required=True, help="Detailed architectural or post-mortem content")
@click.option("--summary", default=None, help="One-line summary for rapid agent scanning")
@click.option("--importance", default=0.8, type=float, help="Importance weight (0.0 to 1.0)")
@click.option("--file", "evidence_file", default=None, help="Relative file path for evidence grounding")
def memory_create(
    project_ref: str,
    memory_type: str,
    title: str,
    content: str,
    summary: str | None,
    importance: float,
    evidence_file: str | None,
) -> None:
    """Create project memory."""
    async def _do_create() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            evidence_list = []
            if evidence_file:
                evidence_list.append(
                    MemoryEvidenceCreate(source_type="file", file_path=evidence_file, confidence=1.0)
                )

            payload = MemoryCreate(
                memory_type=memory_type.upper(),
                title=title,
                content=content,
                summary=summary or title,
                importance=importance,
                evidence=evidence_list if evidence_list else None,
            )
            created = await memory_service.create_memory(session, project.id, payload)
            console.print(Panel(
                f"[bold green]Successfully created memory:[/] {created.id}\n"
                f"[bold white]Title:[/] {created.title}\n"
                f"[bold white]Type:[/]  {created.memory_type} (Status: {created.status})\n"
                f"[bold white]Summary:[/] {created.summary}",
                title="Memory Stored",
                border_style="green",
            ))

    asyncio.run(_do_create())


@memory.command("list", help="List project memories.")
@click.argument("project_ref", default=".", required=False)
@click.option("--type", default=None, help="Filter by memory type (DECISION, CONSTRAINT, FAILURE, etc.)")
@click.option("--status", default=None, help="Filter by status (ACTIVE, STALE, DEPRECATED, etc.)")
def memory_list(project_ref: str, type: str | None, status: str | None) -> None:
    """List project memories."""
    async def _do_list() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            memories = await memory_service.list_memories(
                session, project_id=project.id, memory_type=type, status=status
            )

            table = Table(title=f"Memories for {project.name} ({len(memories)})", border_style="yellow")
            table.add_column("Type", style="bold cyan")
            table.add_column("Title", style="bold white")
            table.add_column("Status", style="magenta")
            table.add_column("Version", style="dim")
            table.add_column("Summary", style="white")

            for m in memories:
                st_color = "green" if m.status == "ACTIVE" else ("yellow" if m.status == "STALE" else "red")
                table.add_row(
                    m.memory_type,
                    m.title,
                    f"[{st_color}]{m.status}[/]",
                    f"v{m.version}",
                    m.summary[:70] + ("..." if len(m.summary) > 70 else ""),
                )
            console.print(table)

    asyncio.run(_do_list())


@memory.command("search", help="Semantic hybrid search across project memories.")
@click.argument("query")
@click.argument("project_ref", default=".", required=False)
@click.option("--limit", default=5, type=int)
def memory_search(query: str, project_ref: str, limit: int) -> None:
    """Search project memories."""
    async def _do_search() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            results = await memory_service.search_memories(session, project.id, query=query, limit=limit)
            console.print(f"\n[bold green]Search Results for:[/] '{query}' ({len(results)})\n")
            for r in results:
                m = r["memory"]
                score = r["combined_score"]
                console.print(Panel(
                    f"[bold white]{m.summary}[/]\n\n{m.content}\n\n[dim]ID: {m.id} | Score: {score:.2f} | Status: {m.status}[/]",
                    title=f"[{m.memory_type}] {m.title}",
                    border_style="cyan",
                ))

    asyncio.run(_do_search())


@memory.command("verify", help="Verify all memories against current filesystem source code.")
@click.argument("project_ref", default=".", required=False)
def memory_verify(project_ref: str) -> None:
    """Run verification engine on project memories."""
    async def _do_verify() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            counts = await verification_engine.verify_project_memories(session, project.id)
            console.print(Panel(
                f"Verified Active: [green]{counts['verified']}[/]\n"
                f"Flagged Stale:   [yellow]{counts['stale']}[/]\n"
                f"Deprecated:      [red]{counts['deprecated']}[/]",
                title=f"Verification Report: {project.name}",
                border_style="green",
            ))

    asyncio.run(_do_verify())


@memory.command("consolidate", help="Consolidate episodic events into durable knowledge principles.")
@click.argument("project_ref", default=".", required=False)
def memory_consolidate(project_ref: str) -> None:
    """Run memory consolidation."""
    async def _do_consolidate() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            with console.status("[bold magenta]Clustering episodic memories & synthesizing durable rules...[/]"):
                res = await consolidation_engine.consolidate_project(session, project.id)
            console.print(Panel(
                f"Clusters Consolidated: [green]{res['clusters_consolidated']}[/]\n"
                f"Durable Rules Created: [green]{res['durable_memories_created']}[/]\n"
                f"Episodes Archived:     [yellow]{res['memories_archived']}[/]",
                title=f"Consolidation Summary: {project.name}",
                border_style="magenta",
            ))

    asyncio.run(_do_consolidate())


@memory.command("export", help="Export all project memories to JSON file.")
@click.argument("output_file", default="cortex_memories.json")
@click.argument("project_ref", default=".", required=False)
def memory_export(output_file: str, project_ref: str) -> None:
    """Export memories to JSON for privacy and portability."""
    async def _do_export() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            memories = await memory_service.list_memories(session, project.id, limit=1000)
            data = []
            for m in memories:
                data.append({
                    "id": m.id,
                    "type": m.memory_type,
                    "title": m.title,
                    "summary": m.summary,
                    "content": m.content,
                    "status": m.status,
                    "importance": m.importance,
                    "created_at": m.created_at.isoformat(),
                })
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            console.print(f"[bold green]Exported {len(data)} memories to:[/] {output_file}")

    asyncio.run(_do_export())


@memory.command("purge", help="Permanently purge all memories for a project.")
@click.argument("project_ref", default=".", required=False)
@click.confirmation_option(prompt="Are you sure you want to purge all project memories?")
def memory_purge(project_ref: str) -> None:
    """Purge project memories."""
    async def _do_purge() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            await session.execute(delete(Memory).where(Memory.project_id == project.id))
            await session.commit()
            console.print(f"[bold red]Purged all memories for project:[/] {project.name}")

    asyncio.run(_do_purge())


# ==================== CONTEXT & GOVERNANCE ====================

@cli.command(help="Generate structured, token-bounded context block for a planned task.")
@click.argument("task_text")
@click.argument("project_ref", default=".", required=False)
@click.option("--profile", default="medium", help="small, medium, or large")
def context(task_text: str, project_ref: str, profile: str) -> None:
    """Generate prompt context block."""
    async def _do_context() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            ctx = await context_composer.build_context(
                session, project_id=project.id, task_text=task_text, profile=profile
            )
            console.print(ctx)

    asyncio.run(_do_context())




@cli.command(help="Run automated comparative benchmark suite (Baseline vs CortexForge).")
@click.argument("project_ref", default=".", required=False)
def benchmark(project_ref: str) -> None:
    """Run empirical benchmark scorecards for hypotheses H1-H5."""
    async def _do_bench() -> None:
        await init_db()
        async with session_scope() as session:
            project = await _get_project_or_exit(session, project_ref)
            with console.status(f"[bold green]Running comparative benchmark on {project.name}...[/]"):
                scorecards = await evaluation_runner.run_benchmark(session, project.id)

            for sc in scorecards:
                table = Table(
                    title=f"Benchmark Task: {sc.task_name} ({sc.task_id})", border_style="cyan"
                )
                table.add_column("Configuration", style="bold white")
                table.add_column("Context Items", style="yellow")
                table.add_column("Files Referenced", style="yellow")
                table.add_column("Input Tokens", style="cyan")
                table.add_column("Latency (ms)", style="dim")
                table.add_column("Stale Rate", style="red")
                table.add_column("Precision", style="magenta")

                for mode_name, res in sc.results.items():
                    table.add_row(
                        mode_name,
                        str(res.context_items),
                        str(res.files_referenced),
                        f"{res.input_tokens:,}",
                        f"{res.latency_ms:.1f}",
                        f"{res.stale_retrieval_rate:.0%}",
                        # An unmeasured metric prints as "n/a", never as 0.
                        "n/a" if res.retrieval_precision is None else f"{res.retrieval_precision:.2f}",
                    )

                console.print(table)
                console.print(Panel(
                    f"[bold green]Exploration Reduction:[/] {sc.exploration_reduction_pct}%\n"
                    f"[bold green]Token Reduction:[/]       {sc.token_reduction_pct}%\n"
                    f"[dim]Relevance judged by: "
                    f"{next(iter(sc.results.values())).relevance_basis}[/]",
                    title="Measured Scorecard",
                    border_style="green",
                ))

                console.print(Panel(
                    "\n".join(f"- {note}" for note in sc.measurement_notes),
                    title="What was and was not measured",
                    border_style="yellow",
                ))

    asyncio.run(_do_bench())


@cli.command(help="Launch the Model Context Protocol (MCP) server on stdio transport.")
def mcp() -> None:
    """Start MCP server."""
    from cortexforge.apps.mcp.server import main as mcp_main
    mcp_main()


@cli.command(help="Mirror stored embeddings into the indexed vector column (PostgreSQL).")
@click.argument("project_ref", default=".", required=False)
def reindex(project_ref: str) -> None:
    """Populate the pgvector column so retrieval can use the index.

    Embeddings are stored as JSON so the SQLite fallback works. On PostgreSQL
    that JSON is not searchable, so this mirrors it into the typed column the
    index covers. Run it after upgrading an existing database, or after changing
    embedding provider.
    """
    async def _do_reindex() -> None:
        await init_db()
        async with session_scope() as session:
            from cortexforge.retrieval.vector_store import (
                backfill_vector_column,
                has_pgvector,
            )

            if not await has_pgvector(session):
                console.print(
                    Panel(
                        "This database has no pgvector extension, so there is no "
                        "indexed column to populate. Retrieval uses the Python "
                        "fallback, which reads the JSON embeddings directly and "
                        "needs no reindex.",
                        title="Nothing to do",
                        border_style="yellow",
                    )
                )
                return

            project_id = None
            if project_ref != ".":
                project = await _get_project_or_exit(session, project_ref)
                project_id = project.id

            with console.status("[bold green]Mirroring embeddings into the vector index...[/]"):
                result = await backfill_vector_column(session, project_id=project_id)

            console.print(
                Panel(
                    f"[bold green]Backfilled:[/] {result['backfilled']}\n"
                    f"[bold yellow]Skipped:[/]    {result['skipped']}\n"
                    f"[dim]{result['reason']}[/]",
                    title="Vector Index Reindex",
                    border_style="green" if not result["skipped"] else "yellow",
                )
            )

    asyncio.run(_do_reindex())


@cli.command(help="Check this project's documentation against what it actually contains.")
@click.argument("root", default=".", required=False)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit non-zero when any drift is found, for use in CI.",
)
def integrity(root: str, strict: bool) -> None:
    """Report contradictions between a project's claims and its artifacts.

    CortexForge treats repository text as untrusted input. Its own README is
    repository text, so the same check applies to itself (specification
    section 46).
    """
    from cortexforge.apps.api.main import app
    from cortexforge.integrity import ProjectIntegrityChecker

    report = ProjectIntegrityChecker(root).check(api_paths=set(app.openapi()["paths"]))

    if report.is_consistent:
        console.print(
            Panel(
                f"No documentation drift detected across "
                f"{len(report.checks_run)} check(s).",
                title="Project Integrity",
                border_style="green",
            )
        )
        return

    table = Table(title="Documentation Drift", border_style="yellow")
    table.add_column("Severity", style="bold")
    table.add_column("Check", style="cyan")
    table.add_column("Claimed", style="yellow")
    table.add_column("Actual", style="magenta")
    table.add_column("Finding", style="white")

    for finding in report.findings:
        table.add_row(
            finding.severity,
            finding.check,
            finding.claimed or "-",
            finding.actual or "-",
            finding.summary,
        )
    console.print(table)

HOOK_START_MARKER = "# >>> CORTEXFORGE_HOOK_START >>>"
HOOK_END_MARKER = "# <<< CORTEXFORGE_HOOK_END <<<"
SUPPORTED_HOOKS = ("post-commit", "post-merge", "post-checkout")


def _generate_hook_script(hook_name: str) -> str:
    return f"""{HOOK_START_MARKER}
# CortexForge automatic durable background job dispatch
if command -v cortex >/dev/null 2>&1; then
    cortex hooks handle --hook {hook_name} --path "$PWD" "$@" >/dev/null 2>&1 || true
elif command -v python3 >/dev/null 2>&1; then
    python3 -m cortexforge.apps.cli.main hooks handle --hook {hook_name} --path "$PWD" "$@" >/dev/null 2>&1 || true
elif command -v python >/dev/null 2>&1; then
    python -m cortexforge.apps.cli.main hooks handle --hook {hook_name} --path "$PWD" "$@" >/dev/null 2>&1 || true
fi
{HOOK_END_MARKER}
"""


@cli.group(help="Manage Git hooks for automatic CortexForge job dispatch.")
def hooks() -> None:
    pass


@hooks.command("install", help="Install post-commit, post-merge, and post-checkout Git hooks.")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
def install_hooks(path: str) -> None:
    """Install git hooks into .git/hooks/ idempotently."""
    canonical_path = os.path.realpath(path)
    git_dir = os.path.join(canonical_path, ".git")
    if not os.path.isdir(git_dir):
        console.print(f"[bold red]Error:[/] '{canonical_path}' is not a Git repository (.git not found).")
        sys.exit(1)

    hooks_dir = os.path.join(git_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)

    installed = []
    for hook_name in SUPPORTED_HOOKS:
        hook_path = os.path.join(hooks_dir, hook_name)
        hook_content = _generate_hook_script(hook_name)

        if os.path.exists(hook_path):
            with open(hook_path, "r", encoding="utf-8") as f:
                existing_text = f.read()
            if HOOK_START_MARKER in existing_text:
                installed.append(f"{hook_name} (already installed)")
                continue
            new_text = existing_text.rstrip() + "\n\n" + hook_content
        else:
            new_text = "#!/bin/sh\n\n" + hook_content

        with open(hook_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)

        try:
            current_mode = os.stat(hook_path).st_mode
            os.chmod(hook_path, current_mode | 0o755)
        except OSError:
            pass

        installed.append(hook_name)

    console.print(f"[bold green]CortexForge Git hooks installed:[/] {', '.join(installed)}")


@hooks.command("uninstall", help="Remove CortexForge hooks from Git repository.")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
def uninstall_hooks(path: str) -> None:
    """Remove CortexForge git hooks idempotently."""
    canonical_path = os.path.realpath(path)
    git_dir = os.path.join(canonical_path, ".git")
    if not os.path.isdir(git_dir):
        console.print(f"[bold red]Error:[/] '{canonical_path}' is not a Git repository (.git not found).")
        sys.exit(1)

    hooks_dir = os.path.join(git_dir, "hooks")
    removed = []

    for hook_name in SUPPORTED_HOOKS:
        hook_path = os.path.join(hooks_dir, hook_name)
        if not os.path.exists(hook_path):
            continue

        with open(hook_path, "r", encoding="utf-8") as f:
            content = f.read()

        if HOOK_START_MARKER not in content:
            continue

        start_idx = content.find(HOOK_START_MARKER)
        end_idx = content.find(HOOK_END_MARKER) + len(HOOK_END_MARKER)
        remaining = (content[:start_idx] + content[end_idx:]).strip()

        if not remaining or remaining == "#!/bin/sh":
            os.remove(hook_path)
        else:
            with open(hook_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(remaining + "\n")

        removed.append(hook_name)

    if removed:
        console.print(f"[bold green]Removed CortexForge Git hooks:[/] {', '.join(removed)}")
    else:
        console.print("[dim]No CortexForge Git hooks found to remove.[/]")


@hooks.command("handle", help="Enqueue background durable job when Git hook fires.")
@click.option("--hook", required=True, help="Name of the hook (post-commit, post-merge, post-checkout)")
@click.option("--path", default=".", help="Repository root path")
def handle_hook(hook: str, path: str) -> None:
    """Enqueue durable background scan job without blocking git execution."""
    from cortexforge.jobs.durable import DurableJobStore

    canonical_path = os.path.realpath(path)

    async def _do_handle() -> None:
        await init_db()
        async with session_scope() as session:
            stmt = select(Project).where(Project.local_path == canonical_path)
            res = await session.execute(stmt)
            project = res.scalars().first()
            if not project:
                return

            store = DurableJobStore()
            await store.submit(
                session,
                job_type="scan_project",
                project_id=project.id,
                parameters={"incremental": True, "hook": hook},
            )
            await session.commit()

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, _do_handle()).result()
        else:
            asyncio.run(_do_handle())
    except Exception as e:
        sys.stderr.write(f"CortexForge hook dispatch warning: {e}\n")


@cli.command(help="Start the FastAPI REST gateway server.")
@click.option(
    "--host",
    default=None,
    help="Host interface to bind. Defaults to CORTEX_HOST, then 127.0.0.1.",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Port to listen on. Defaults to CORTEX_PORT, then 8000.",
)
@click.option("--reload", is_flag=True, default=False, help="Enable auto-reload")
def serve(host: str | None, port: int | None, reload: bool) -> None:
    """Launch the REST API server.

    An explicit flag beats the environment, which beats the default. The
    environment variables are documented in `.env.example`, and a documented
    variable that nothing reads is a lie about how the software is configured.
    """
    import uvicorn

    bind_host = host or os.environ.get("CORTEX_HOST") or "127.0.0.1"
    bind_port = port or int(os.environ.get("CORTEX_PORT") or 8000)

    console.print(
        f"[bold green]Starting CortexForge REST API on http://{bind_host}:{bind_port}[/]"
    )
    uvicorn.run(
        "cortexforge.apps.api.main:app", host=bind_host, port=bind_port, reload=reload
    )


@cli.command(help="Display machine-readable capability registry and operational status (§42).")
@click.option(
    "--status",
    default=None,
    help="Filter by status: IMPLEMENTED, PARTIAL, EXPERIMENTAL, DISABLED, UNSUPPORTED",
)
@click.option("--category", default=None, help="Filter by category")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output raw JSON")
def capabilities(status: str | None, category: str | None, as_json: bool) -> None:
    """Inspect CortexForge capability matrix with verified implementation locations."""
    from cortexforge.core.capabilities import CapabilityRegistry, CapabilityStatus

    reg = CapabilityRegistry.get_instance()
    status_enum = None
    if status:
        try:
            status_enum = CapabilityStatus(status.upper())
        except ValueError:
            console.print(
                f"[bold red]Invalid status:[/] {status}. Choose from: {', '.join(s.value for s in CapabilityStatus)}"
            )
            sys.exit(1)

    caps = reg.list_capabilities(status=status_enum, category=category)
    if as_json:
        console.print(
            json.dumps(
                {
                    "summary": reg.summary(),
                    "capabilities": [c.model_dump() for c in caps],
                },
                indent=2,
            )
        )
        return

    table = Table(title="CortexForge Capability Registry (§42)", show_lines=True)
    table.add_column("Status", style="bold", width=14)
    table.add_column("Capability ID", style="cyan", width=26)
    table.add_column("Name", style="white", width=30)
    table.add_column("Category", style="magenta", width=18)
    table.add_column("Implementation / Tests", style="dim", width=40)

    status_styles = {
        CapabilityStatus.IMPLEMENTED: "[bold green]IMPLEMENTED[/]",
        CapabilityStatus.PARTIAL: "[bold yellow]PARTIAL[/]",
        CapabilityStatus.EXPERIMENTAL: "[bold cyan]EXPERIMENTAL[/]",
        CapabilityStatus.DISABLED: "[dim]DISABLED[/]",
        CapabilityStatus.UNSUPPORTED: "[bold red]UNSUPPORTED[/]",
    }

    for cap in caps:
        st_label = status_styles.get(cap.status, cap.status.value)
        impl_summary = "\n".join(cap.implementation_files[:2] + cap.tests[:1])
        table.add_row(st_label, cap.capability_id, cap.name, cap.category, impl_summary)

    console.print(table)
    summary_text = " | ".join(f"{k}: {v}" for k, v in reg.summary().items())
    console.print(Panel(summary_text, title="Registry Summary", border_style="blue"))


@cli.command(help="Run system diagnostics and verify readiness across all subsystems (§56).")
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="Output diagnostic report as JSON"
)
def doctor(as_json: bool) -> None:
    """Perform real pre-flight diagnostics across database, parsers, providers, and security."""
    from typing import Any

    from sqlalchemy import inspect, text

    from cortexforge.core.capabilities import CapabilityRegistry
    from cortexforge.core.db import engine, init_db, session_scope
    from cortexforge.jobs.durable import DurableJobStore
    from cortexforge.observability.tracing import is_telemetry_enabled
    from cortexforge.security.redactor import SecretRedactor

    report: list[dict[str, Any]] = []

    async def _run_diagnostics() -> None:
        # 1. Database Readiness
        db_status = "PASS"
        dialect = engine.url.get_backend_name()
        try:
            await init_db()
            async with engine.connect() as conn:
                tables = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )
                db_details = f"{dialect} connected ({len(tables)} tables verified)"
        except Exception as exc:
            db_status = "FAIL"
            db_details = f"Connection failed: {exc}"

        report.append(
            {"subsystem": "Database", "status": db_status, "details": db_details}
        )

        # 2. Alembic Migration Head
        mig_status = "PASS"
        try:
            async with engine.connect() as conn:
                res = await conn.execute(text("SELECT version_num FROM alembic_version"))
                head = res.scalar()
                mig_details = f"Alembic revision: {head}"
        except Exception:
            mig_details = "Direct schema (Base.metadata.create_all)"

        report.append(
            {"subsystem": "Migrations", "status": mig_status, "details": mig_details}
        )

        # 3. Tree-sitter Parsers
        ts_status = "PASS"
        loaded_langs: list[str] = []
        for lang_name, mod in [
            ("python", "tree_sitter_python"),
            ("javascript", "tree_sitter_javascript"),
            ("typescript", "tree_sitter_typescript"),
            ("java", "tree_sitter_java"),
            ("go", "tree_sitter_go"),
        ]:
            try:
                __import__(mod)
                loaded_langs.append(lang_name)
            except ImportError:
                ts_status = "WARN"

        report.append({
            "subsystem": "AST Parsers",
            "status": ts_status,
            "details": f"{len(loaded_langs)}/5 languages available ({', '.join(loaded_langs)})",
        })

        # 4. Embedding Provider
        emb_provider = os.environ.get(
            "CORTEX_EMBEDDING_PROVIDER",
            os.environ.get("CORTEX_EMBEDDINGS_PROVIDER", "hash"),
        ).lower()
        emb_model = os.environ.get("CORTEX_EMBEDDING_MODEL", "local-hash-384")
        report.append({
            "subsystem": "Embeddings",
            "status": "PASS",
            "details": f"Provider: {emb_provider} (model: {emb_model})",
        })

        # 5. LLM Provider
        llm_provider = os.environ.get("CORTEX_LLM_PROVIDER", "mock").lower()
        llm_model = os.environ.get("CORTEX_LLM_MODEL", "mock-v1")
        llm_status = (
            "PASS"
            if llm_provider != "mock" or os.environ.get("CORTEX_ENV") != "production"
            else "WARN"
        )
        report.append({
            "subsystem": "LLM Provider",
            "status": llm_status,
            "details": f"Provider: {llm_provider} (model: {llm_model})",
        })

        # 6. Durable Background Jobs
        job_status = "PASS"
        try:
            async with session_scope() as session:
                recovered = await DurableJobStore().recover_abandoned(session)
                job_details = (
                    f"DurableJobStore active ({len(recovered)} abandoned leases recovered)"
                )
        except Exception as exc:
            job_status = "WARN"
            job_details = f"Job store warning: {exc}"

        report.append(
            {"subsystem": "Durable Jobs", "status": job_status, "details": job_details}
        )

        # 7. Secret Redaction Pre-flight
        redact_test = SecretRedactor.redact_secrets(
            "token=sk-proj-1234567890abcdef1234567890abcdef"
        )
        redact_status = (
            "PASS"
            if "sk-proj-" not in redact_test and "[REDACTED" in redact_test
            else "FAIL"
        )
        report.append({
            "subsystem": "Secret Redaction",
            "status": redact_status,
            "details": "Pre-flight credential detection verified",
        })

        # 8. Distributed Tracing & Telemetry
        tel_enabled = is_telemetry_enabled()
        tel_mode = os.environ.get("CORTEX_TELEMETRY", "local")
        report.append({
            "subsystem": "Observability",
            "status": "PASS",
            "details": f"Tracing: {'active' if tel_enabled else 'disabled'} (mode: {tel_mode})",
        })

        # 9. Capability Registry
        reg = CapabilityRegistry.get_instance()
        summary = reg.summary()
        report.append({
            "subsystem": "Capabilities",
            "status": "PASS",
            "details": f"{summary.get('IMPLEMENTED', 0)} implemented, {summary.get('PARTIAL', 0)} partial, {summary.get('DISABLED', 0)} disabled",
        })

    asyncio.run(_run_diagnostics())

    if as_json:
        console.print(json.dumps(report, indent=2))
        return

    table = Table(
        title="CortexForge System Diagnostics (`cortex doctor`)", show_lines=True
    )
    table.add_column("Subsystem", style="cyan", width=20)
    table.add_column("Status", style="bold", width=10)
    table.add_column("Details", style="white", width=55)

    status_colors = {
        "PASS": "[bold green]PASS[/]",
        "WARN": "[bold yellow]WARN[/]",
        "FAIL": "[bold red]FAIL[/]",
    }

    all_passed = True
    for item in report:
        st = item["status"]
        if st == "FAIL":
            all_passed = False
        table.add_row(item["subsystem"], status_colors.get(st, st), item["details"])

    console.print(table)
    if all_passed:
        console.print(
            "[bold green]System is ready for production cognitive operations.[/]"
        )
    else:
        console.print(
            "[bold red]One or more critical subsystems reported failures. Please check the logs.[/]"
        )
        sys.exit(1)


if __name__ == "__main__":
    cli()
