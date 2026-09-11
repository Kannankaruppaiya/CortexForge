"""Incremental repository scanner and symbol extractor."""

import hashlib
import os
import time

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.config_intelligence import (
    discover_config_files,
    read_artifact,
)
from cortexforge.code_intelligence.git_provider import GitProvider
from cortexforge.code_intelligence.lineage import SymbolLineageTracker
from cortexforge.code_intelligence.parser import ParseResult
from cortexforge.code_intelligence.treesitter.analyzer import TreeSitterProvider
from cortexforge.core.models import (
    CodeEntity,
    Project,
    Relationship,
    RepositorySnapshot,
)
from cortexforge.core.schemas import ScanResponse
from cortexforge.security.path_safety import (
    PathSecurity,
    SafeFileReader,
)

# Hard resource limits to prevent denial-of-service from hostile repositories (§25)
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB per file
MAX_TOTAL_SCAN_BYTES = 100 * 1024 * 1024  # 100 MB total per scan
MAX_SCAN_DURATION_SECONDS = 120.0  # 2 minute timeout
MAX_ALLOWED_FILES = 20_000


def get_git_head_commit(root_path: str) -> str | None:
    """Safely get current git HEAD commit SHA using hardened GitProvider."""
    try:
        return GitProvider(root_path).get_head_commit()
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

    def __init__(
        self,
        provider: TreeSitterProvider | None = None,
        lineage: SymbolLineageTracker | None = None,
    ) -> None:
        self.lineage = lineage or SymbolLineageTracker()
        self.provider = provider or TreeSitterProvider()

    def discover_files(self, root_path: str, max_files: int | None = None) -> list[str]:
        """Traverse directory tree and collect parseable source files."""
        matched_files: list[str] = []
        canonical_root = os.path.realpath(root_path)
        effective_max = min(max_files or MAX_ALLOWED_FILES, MAX_ALLOWED_FILES)
        accumulated_bytes = 0

        for dirpath, dirnames, filenames in os.walk(canonical_root):
            # Prune ignored directories in-place
            dirnames[:] = [
                d
                for d in dirnames
                if d not in DEFAULT_IGNORED_DIRS and not d.startswith(".")
            ]

            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in DEFAULT_IGNORED_EXTS or not self.provider.can_parse(fname):
                    continue

                full_path = os.path.join(dirpath, fname)
                try:
                    fsize = os.path.getsize(full_path)
                except OSError:
                    continue

                # Hard size limits: skip oversized files (>5MB) and break if total bytes exceeded (>100MB)
                if fsize > MAX_FILE_SIZE_BYTES:
                    continue
                if accumulated_bytes + fsize > MAX_TOTAL_SCAN_BYTES:
                    return matched_files
                accumulated_bytes += fsize

                # Store path relative to canonical_root for consistency across platforms
                rel_path = os.path.relpath(full_path, canonical_root).replace("\\", "/")
                if not PathSecurity.is_safe_subpath(canonical_root, rel_path):
                    continue
                matched_files.append(rel_path)

                if len(matched_files) >= effective_max:
                    return matched_files

        return matched_files

    async def _index_config_artifacts(
        self,
        session: AsyncSession,
        project: Project,
        canonical_root: str,
        created_entities_by_qualified: dict[str, CodeEntity],
    ) -> int:
        """Index configuration files as entities, one per artifact and per key.

        Both granularities matter. The file-level entity is what a change to the
        artifact anchors to; the key-level entities are what a claim about one
        specific setting -- "the service requires REDIS_URL" -- can be grounded
        in, so that removing that one key invalidates that one memory rather than
        everything touching the file.
        """
        artifacts = discover_config_files(canonical_root, DEFAULT_IGNORED_DIRS)
        indexed = 0

        for relative_path in artifacts:
            artifact = read_artifact(canonical_root, relative_path)
            if artifact is None:
                continue

            config_metadata = {
                "config_kind": artifact.kind,
                "evidence_type": artifact.evidence_type,
                "key_count": len(artifact.keys),
                **artifact.detail,
            }

            file_entity = created_entities_by_qualified.get(artifact.qualified_name)
            if file_entity is not None:
                # A file can be both code and configuration -- an Alembic
                # migration is Python *and* a schema change -- so the code
                # entity already exists here. Its metadata is merged rather than
                # replaced, and merged unconditionally: gating on the content
                # hash would leave a pre-existing code entity permanently
                # without its schema classification.
                file_entity.entity_metadata = {
                    **(file_entity.entity_metadata or {}),
                    **config_metadata,
                }
            else:
                file_entity = CodeEntity(
                    project_id=project.id,
                    entity_type="config",
                    name=os.path.basename(artifact.file_path),
                    qualified_name=artifact.qualified_name,
                    file_path=artifact.file_path,
                    start_line=1,
                    end_line=max(1, len(artifact.keys)),
                    signature=None,
                    content_hash=artifact.content_hash,
                    language=artifact.kind,
                    entity_metadata=config_metadata,
                )
                session.add(file_entity)
                created_entities_by_qualified[artifact.qualified_name] = file_entity
                indexed += 1

            for key in artifact.keys:
                qualified = f"{artifact.file_path}:{key.name}"
                # A key's identity is its name and location; its content hash
                # covers the value preview so that changing a setting -- not just
                # renaming it -- registers as a change.
                key_hash = hashlib.sha256(
                    f"{qualified}|{key.value_preview or ''}".encode()
                ).hexdigest()

                existing_key = created_entities_by_qualified.get(qualified)
                if existing_key is not None:
                    if existing_key.content_hash != key_hash:
                        existing_key.content_hash = key_hash
                        existing_key.start_line = key.line
                        existing_key.end_line = key.line
                    continue

                key_entity = CodeEntity(
                    project_id=project.id,
                    entity_type="config_key",
                    name=key.name,
                    qualified_name=qualified,
                    file_path=artifact.file_path,
                    start_line=key.line,
                    end_line=key.line,
                    signature=None,
                    content_hash=key_hash,
                    language=artifact.kind,
                    entity_metadata={
                        "config_kind": artifact.kind,
                        "evidence_type": artifact.evidence_type,
                        "key": key.name,
                    },
                )
                session.add(key_entity)
                created_entities_by_qualified[qualified] = key_entity
                indexed += 1

        await session.flush()
        return indexed

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
            diff_files = git.get_modified_files(
                base_commit=project.last_indexed_commit, target_commit="HEAD"
            )
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
                existing_snaps = (
                    await session.execute(
                        select(func.count(RepositorySnapshot.id)).where(
                            RepositorySnapshot.project_id == project.id
                        )
                    )
                ).scalar() or 0
                return ScanResponse(
                    project_id=project.id,
                    files_scanned=0,
                    entities_extracted=0,
                    relationships_extracted=0,
                    graph_generation=max(1, existing_snaps),
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
        existing_entities_stmt = select(CodeEntity).where(
            CodeEntity.project_id == project.id
        )
        existing_result = await session.execute(existing_entities_stmt)
        existing_by_qualified: dict[str, CodeEntity] = {
            e.qualified_name: e for e in existing_result.scalars().all()
        }

        total_entities_extracted = 0
        total_relationships_extracted = 0
        all_parsed_relationships = []
        created_entities_by_qualified: dict[str, CodeEntity] = dict(
            existing_by_qualified
        )

        safe_reader = SafeFileReader()
        for rel_path in rel_files:
            try:
                content = safe_reader.read_bytes(canonical_root, rel_path)
            except Exception as e:
                errors.append(f"Failed to read file {rel_path}: {e}")
                continue

            # Skip binary files disguised as source (§25)
            if b"\x00" in content[:8192]:
                continue

            # Check scan timeout
            if time.perf_counter() - start_time > MAX_SCAN_DURATION_SECONDS:
                errors.append(
                    "Scan terminated early: maximum scan duration (120s) exceeded."
                )
                break

            parse_result: ParseResult = self.provider.parse_source(rel_path, content)
            if parse_result.error:
                errors.append(f"Parse error in {rel_path}: {parse_result.error}")
                continue

            if incremental:
                # If an existing entity in this file was removed, clean it and its relationships
                file_existing = [
                    e for e in existing_by_qualified.values() if e.file_path == rel_path
                ]
                new_qnames = {sym.qualified_name for sym in parse_result.symbols}
                removed_entities = [
                    e for e in file_existing if e.qualified_name not in new_qnames
                ]
                if removed_entities:
                    removed_ids = [e.id for e in removed_entities]
                    await session.execute(
                        delete(Relationship).where(
                            (Relationship.project_id == project.id)
                            & (
                                Relationship.source_entity_id.in_(removed_ids)
                                | Relationship.target_entity_id.in_(removed_ids)
                            )
                        )
                    )
                    await session.execute(
                        delete(CodeEntity).where(CodeEntity.id.in_(removed_ids))
                    )
                    for re in removed_entities:
                        created_entities_by_qualified.pop(re.qualified_name, None)
                        existing_by_qualified.pop(re.qualified_name, None)

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
        # In an incremental scan, preserve relationships whose source is an untouched file
        # (inbound edges to rescanned entities, and untouched internal edges).
        # Only delete outbound relationships from the rescanned files.
        if incremental:
            rescanned_files_set = set(rel_files)
            rescanned_entity_ids = [
                e.id
                for e in created_entities_by_qualified.values()
                if e.file_path in rescanned_files_set
            ]
            if rescanned_entity_ids:
                await session.execute(
                    delete(Relationship).where(
                        (Relationship.project_id == project.id)
                        & Relationship.source_entity_id.in_(rescanned_entity_ids)
                    )
                )
        else:
            await session.execute(
                delete(Relationship).where(Relationship.project_id == project.id)
            )
        await session.flush()

        seen_rel_keys: set[tuple[str, str, str]] = set()
        for rel in all_parsed_relationships:
            src_entity = created_entities_by_qualified.get(rel.source_qualified_name)
            tgt_entity = created_entities_by_qualified.get(rel.target_qualified_name)
            if src_entity and tgt_entity:
                rel_key = (src_entity.id, tgt_entity.id, rel.relationship_type)
                if rel_key in seen_rel_keys:
                    continue
                seen_rel_keys.add(rel_key)
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

        # Index configuration, schema and API-contract artifacts alongside code.
        #
        # These are recorded as CodeEntity rows on purpose: a schema change and a
        # signature change should reach memory through one change-impact pipeline
        # rather than two that drift apart (section 14).
        config_entities = await self._index_config_artifacts(
            session, project, canonical_root, created_entities_by_qualified
        )
        total_entities_extracted += config_entities

        # Record Snapshot
        head_commit = get_git_head_commit(canonical_root)
        if head_commit:
            project.last_indexed_commit = head_commit

        # Give every newly seen symbol a durable logical identity, so that a later
        # rename can be recognised as the same symbol moving rather than as one
        # symbol vanishing and another appearing (section 11). Symbols already
        # tracked are untouched, so re-scanning an unchanged project writes nothing.
        await self.lineage.observe_entities(
            session,
            project.id,
            list(created_entities_by_qualified.values()),
            commit_sha=head_commit,
            branch=project.default_branch,
        )

        total_entities_count = (
            await session.execute(
                select(func.count(CodeEntity.id)).where(
                    CodeEntity.project_id == project.id
                )
            )
        ).scalar() or 0

        total_relationships_count = (
            await session.execute(
                select(func.count(Relationship.id)).where(
                    Relationship.project_id == project.id
                )
            )
        ).scalar() or 0

        total_files_count = (
            await session.execute(
                select(func.count(func.distinct(CodeEntity.file_path))).where(
                    CodeEntity.project_id == project.id
                )
            )
        ).scalar() or 0

        existing_snapshots_count = (
            await session.execute(
                select(func.count(RepositorySnapshot.id)).where(
                    RepositorySnapshot.project_id == project.id
                )
            )
        ).scalar() or 0
        graph_gen = existing_snapshots_count + 1

        snapshot = RepositorySnapshot(
            project_id=project.id,
            commit_sha=head_commit or project.last_indexed_commit or "initial-scan",
            file_count=total_files_count if incremental else len(rel_files),
            symbol_count=total_entities_count,
            dependency_count=total_relationships_count,
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
            graph_generation=graph_gen,
            duration_ms=round(duration_ms, 2),
            status="SUCCESS" if not errors else "PARTIAL_SUCCESS",
            errors=errors,
        )
