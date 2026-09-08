"""Incremental repository scanner and symbol extractor."""

import os
import subprocess
import time

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.git_provider import GitProvider
from cortexforge.code_intelligence.parser import ParseResult
from cortexforge.code_intelligence.treesitter.analyzer import TreeSitterProvider
from cortexforge.core.models import (
    CodeEntity,
    Project,
    Relationship,
    RepositorySnapshot,
)
from cortexforge.core.schemas import ScanResponse


def get_git_head_commit(root_path: str) -> str | None:
    """Safely get current git HEAD commit SHA without shell injection."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        sha = res.stdout.strip()
        return sha if len(sha) == 40 else None
    except Exception:
        return None

DEFAULT_IGNORED_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".next",
    "dist",
    "build",
    ".idea",
    ".vscode",
    ".gemini",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

DEFAULT_IGNORED_EXTS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".pyc",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}


class RepositoryScanner:
    """Scans codebase on disk, extracts AST symbols, and persists entities to database."""

    def __init__(self, provider: TreeSitterProvider | None = None) -> None:
        self.provider = provider or TreeSitterProvider()

    def discover_files(self, root_path: str, max_files: int | None = None) -> list[str]:
        """Traverse directory tree and collect parseable source files."""
        matched_files: list[str] = []
        canonical_root = os.path.realpath(root_path)

        for dirpath, dirnames, filenames in os.walk(canonical_root):
            # Prune ignored directories in-place
            dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORED_DIRS and not d.startswith(".")]

            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in DEFAULT_IGNORED_EXTS or not self.provider.can_parse(fname):
                    continue

                full_path = os.path.join(dirpath, fname)
                # Store path relative to canonical_root for consistency across platforms
                rel_path = os.path.relpath(full_path, canonical_root).replace("\\", "/")
                matched_files.append((full_path, rel_path))

                if max_files and len(matched_files) >= max_files:
                    return [rel for _, rel in matched_files]

        return [rel for _, rel in matched_files]

    async def scan_project(
        self,
        session: AsyncSession,
        project: Project,
        incremental: bool = True,
        max_files: int | None = None,
    ) -> ScanResponse:
        """Perform full or incremental AST scan of project repository."""
        start_time = time.perf_counter()
        canonical_root = os.path.realpath(project.local_path)
        if not os.path.exists(canonical_root):
            return ScanResponse(
                project_id=project.id,
                files_scanned=0,
                entities_extracted=0,
                relationships_extracted=0,
                duration_ms=0.0,
                status="FAILED",
                errors=[f"Directory does not exist: {project.local_path}"],
            )

        errors: list[str] = []
        is_git_incremental = False
        git = GitProvider(canonical_root)
        head_commit = git.get_head_commit() or get_git_head_commit(canonical_root)

        if incremental and project.last_indexed_commit and head_commit:
            diff_files = git.get_modified_files(base_commit=project.last_indexed_commit, target_commit="HEAD")
            if diff_files:
                is_git_incremental = True
                # Clean up deleted files from entities
                for df in diff_files:
                    if df.status == "D":
                        await session.execute(
                            delete(Relationship).where(
                                (Relationship.project_id == project.id)
                                & (
                                    Relationship.source_entity_id.in_(
                                        select(CodeEntity.id).where(
                                            CodeEntity.project_id == project.id,
                                            CodeEntity.file_path == df.file_path,
                                        )
                                    )
                                    | Relationship.target_entity_id.in_(
                                        select(CodeEntity.id).where(
                                            CodeEntity.project_id == project.id,
                                            CodeEntity.file_path == df.file_path,
                                        )
                                    )
                                )
                            )
                        )
                        await session.execute(
                            delete(CodeEntity).where(
                                CodeEntity.project_id == project.id,
                                CodeEntity.file_path == df.file_path,
                            )
                        )
                # Only scan modified/added/renamed files
                rel_files = [
                    df.file_path
                    for df in diff_files
                    if df.status != "D"
                    and os.path.exists(os.path.join(canonical_root, df.file_path))
                    and self.provider.can_parse(df.file_path)
                ]
            elif project.last_indexed_commit == head_commit:
                # No changes between last indexed commit and HEAD
                return ScanResponse(
                    project_id=project.id,
                    files_scanned=0,
                    entities_extracted=0,
                    relationships_extracted=0,
                    duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    status="SUCCESS",
                )

        if not is_git_incremental:
            rel_files = self.discover_files(canonical_root, max_files=max_files)

        # If not incremental, clear previous entities and relationships
        if not incremental:
            await session.execute(
                delete(Relationship).where(Relationship.project_id == project.id)
            )
            await session.execute(
                delete(CodeEntity).where(CodeEntity.project_id == project.id)
            )
            await session.flush()

        # Load existing entities for content-hash caching
        existing_entities_stmt = select(CodeEntity).where(CodeEntity.project_id == project.id)
        existing_result = await session.execute(existing_entities_stmt)
        existing_by_qualified: dict[str, CodeEntity] = {
            e.qualified_name: e for e in existing_result.scalars().all()
        }

        total_entities_extracted = 0
        total_relationships_extracted = 0
        all_parsed_relationships = []
        created_entities_by_qualified: dict[str, CodeEntity] = dict(existing_by_qualified)

        for rel_path in rel_files:
            abs_path = os.path.join(canonical_root, rel_path)
            try:
                with open(abs_path, "rb") as f:
                    content = f.read()
            except Exception as e:
                errors.append(f"Failed to read file {rel_path}: {e}")
                continue

            parse_result: ParseResult = self.provider.parse_source(rel_path, content)
            if parse_result.error:
                errors.append(f"Parse error in {rel_path}: {parse_result.error}")
                continue

            for sym in parse_result.symbols:
                existing_entity = created_entities_by_qualified.get(sym.qualified_name)
                if existing_entity:
                    # Update if hash changed
                    if existing_entity.content_hash != sym.content_hash:
                        existing_entity.start_line = sym.start_line
                        existing_entity.end_line = sym.end_line
                        existing_entity.signature = sym.signature
                        existing_entity.content_hash = sym.content_hash
                        existing_entity.entity_metadata = sym.metadata
                    entity_obj = existing_entity
                else:
                    entity_obj = CodeEntity(
                        project_id=project.id,
                        entity_type=sym.entity_type,
                        name=sym.name,
                        qualified_name=sym.qualified_name,
                        file_path=sym.file_path,
                        start_line=sym.start_line,
                        end_line=sym.end_line,
                        signature=sym.signature,
                        content_hash=sym.content_hash,
                        language=sym.language,
                        entity_metadata=sym.metadata,
                    )
                    session.add(entity_obj)
                    created_entities_by_qualified[sym.qualified_name] = entity_obj
                    total_entities_extracted += 1

            all_parsed_relationships.extend(parse_result.relationships)

        await session.flush()

        # Persist Relationships
        # Clear existing relationships to avoid duplicates
        await session.execute(
            delete(Relationship).where(Relationship.project_id == project.id)
        )
        await session.flush()

        for rel in all_parsed_relationships:
            src_entity = created_entities_by_qualified.get(rel.source_qualified_name)
            tgt_entity = created_entities_by_qualified.get(rel.target_qualified_name)
            if src_entity and tgt_entity:
                rel_obj = Relationship(
                    project_id=project.id,
                    source_entity_id=src_entity.id,
                    target_entity_id=tgt_entity.id,
                    relationship_type=rel.relationship_type,
                    confidence=rel.confidence,
                    source=rel.source,
                )
                session.add(rel_obj)
                total_relationships_extracted += 1

        # Record Snapshot
        head_commit = get_git_head_commit(canonical_root)
        if head_commit:
            project.last_indexed_commit = head_commit

        snapshot = RepositorySnapshot(
            project_id=project.id,
            commit_sha=head_commit or project.last_indexed_commit or "initial-scan",
            file_count=len(rel_files),
            symbol_count=len(created_entities_by_qualified),
            dependency_count=total_relationships_extracted,
        )
        session.add(snapshot)

        project.status = "READY"
        await session.commit()

        duration_ms = (time.perf_counter() - start_time) * 1000
        return ScanResponse(
            project_id=project.id,
            files_scanned=len(rel_files),
            entities_extracted=total_entities_extracted,
            relationships_extracted=total_relationships_extracted,
            duration_ms=round(duration_ms, 2),
            status="SUCCESS" if not errors else "PARTIAL_SUCCESS",
            errors=errors,
        )
