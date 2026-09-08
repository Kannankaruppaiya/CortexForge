"""Command Line Interface for CortexForge."""

import asyncio
import os

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from sqlalchemy import func, select

from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import CodeEntity, Project
from cortexforge.graph.service import GraphService

console = Console()
scanner = RepositoryScanner()
graph_service = GraphService()


@click.group(help="CortexForge: Evolving, verified project memory layer for AI coding agents.")
def cli() -> None:
    pass


@cli.command(help="Scan repository, extract AST entities, and persist project model.")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--incremental/--full", default=True, help="Perform incremental or full scan")
@click.option("--name", default=None, help="Project name (defaults to folder name)")
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
                console.print(f"[bold green]Registering new project:[/] {proj_name} at `{canonical_path}`")
                project = Project(
                    name=proj_name,
                    local_path=canonical_path,
                    status="INITIALIZING",
                )
                session.add(project)
                await session.flush()
            else:
                console.print(f"[bold cyan]Scanning registered project:[/] {project.name}")

            with console.status("[bold blue]Parsing AST code entities via Tree-sitter...[/]"):
                scan_res = await scanner.scan_project(
                    session, project, incremental=incremental
                )

            # Display results in Rich table
            table = Table(title=f"Scan Summary: {project.name}", border_style="cyan")
            table.add_column("Metric", style="bold white")
            table.add_column("Value", style="green")

            table.add_row("Files Processed", str(scan_res.files_scanned))
            table.add_row("Entities Extracted", str(scan_res.entities_extracted))
            table.add_row("Relationships Mapped", str(scan_res.relationships_extracted))
            table.add_row("Duration", f"{scan_res.duration_ms:.2f} ms")
            table.add_row("Status", f"[bold green]{scan_res.status}[/]" if scan_res.status == "SUCCESS" else f"[bold yellow]{scan_res.status}[/]")

            console.print(table)
            if scan_res.errors:
                console.print(f"[bold yellow]Warnings ({len(scan_res.errors)}):[/]")
                for err in scan_res.errors[:5]:
                    console.print(f"  - {err}")

    asyncio.run(_do_scan())


@cli.command(help="Display synthesized project structural architecture.")
@click.argument("project_ref", default=".", required=False)
@click.option("--depth", default=2, type=int, help="Traversal depth")
def architecture(project_ref: str, depth: int) -> None:
    """Show project architecture tree and components."""
    async def _do_arch() -> None:
        await init_db()
        async with session_scope() as session:
            # Resolve project
            project = None
            if os.path.exists(project_ref):
                canon = os.path.realpath(project_ref)
                res = await session.execute(select(Project).where(Project.local_path == canon))
                project = res.scalars().first()

            if not project:
                project = await session.get(Project, project_ref)

            if not project:
                # Try finding by name
                res = await session.execute(select(Project).where(Project.name == project_ref))
                project = res.scalars().first()

            if not project:
                console.print(f"[bold red]Error:[/] Project '{project_ref}' not found in database.")
                console.print("Run `cortex scan .` first to index your project.")
                return

            arch = await graph_service.get_project_architecture(session, project.id, depth=depth)
            if not arch:
                console.print(f"[bold red]Error:[/] Could not compute architecture for {project.name}.")
                return

            # Header Panel
            hdr = f"[bold cyan]{arch.project_name}[/]\n"
            hdr += f"Files: [green]{arch.total_files}[/] | Entities: [green]{arch.total_entities}[/] | Relationships: [green]{arch.total_relationships}[/]\n"
            hdr += f"Languages: [magenta]{', '.join(arch.languages)}[/]"
            console.print(Panel(hdr, title="Project Architecture Overview", border_style="cyan"))

            # Tree of modules
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


@cli.command(help="Show CortexForge status and registered projects.")
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
            table.add_column("Status", style="magenta")

            for p in projects:
                ecount = await session.scalar(
                    select(func.count(CodeEntity.id)).where(CodeEntity.project_id == p.id)
                )
                table.add_row(
                    p.id[:8] + "...",
                    p.name,
                    p.local_path,
                    str(ecount or 0),
                    p.status,
                )

            console.print(table)

    asyncio.run(_do_status())


@cli.command(help="Launch the Model Context Protocol (MCP) server on stdio transport.")
def mcp() -> None:
    """Start MCP server."""
    from cortexforge.apps.mcp.server import main as mcp_main
    mcp_main()


@cli.command(help="Start the FastAPI REST gateway server.")
@click.option("--host", default="127.0.0.1", help="Host interface to bind")
@click.option("--port", default=8000, type=int, help="Port to listen on")
@click.option("--reload", is_flag=True, default=False, help="Enable auto-reload")
def serve(host: str, port: int, reload: bool) -> None:
    """Launch REST API server."""
    import uvicorn
    console.print(f"[bold green]Starting CortexForge REST API on http://{host}:{port}[/]")
    uvicorn.run("cortexforge.apps.api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    cli()
