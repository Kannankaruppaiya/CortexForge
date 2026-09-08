"""Memory Provenance Graph and Auditing Engine.

Answers: 'Why does CortexForge believe this?'
Traceable chain:
Memory -> Evidence -> File -> Symbol -> Relationship -> Change -> Commit -> Task -> Test Result -> Review.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.core.models import (
    CodeEntity,
    Commit,
    Memory,
    MemoryEvidence,
    MemoryVersion,
    SymbolChange,
    TestCaseResult,
    TestRun,
)


@dataclass
class ProvenanceEvidenceItem:
    evidence_id: str
    source_type: str
    file_path: str
    line_range: tuple[int, int] | None
    symbol_name: str | None = None
    symbol_signature: str | None = None
    commit_sha: str | None = None
    verification_status: str = "UNVERIFIED"


@dataclass
class MemoryProvenanceReport:
    memory_id: str
    title: str
    status: str
    layer: str
    source_type: str
    source_authority: float
    confidence: float
    why_believed: str
    created_at: str
    last_verified_at: str | None
    evidences: list[ProvenanceEvidenceItem] = field(default_factory=list)
    version_history: list[dict[str, Any]] = field(default_factory=list)
    associated_commits: list[dict[str, str]] = field(default_factory=list)
    associated_tests: list[dict[str, str]] = field(default_factory=list)


class ProvenanceEngine:
    """Reconstructs the full causal and evidentiary chain for any durable memory."""

    @classmethod
    async def get_provenance(
        cls, session: AsyncSession, memory_id: str
    ) -> MemoryProvenanceReport | None:
        """Fetch full provenance chain for a specific memory."""
        stmt = (
            select(Memory)
            .options(
                selectinload(Memory.evidences),
                selectinload(Memory.versions),
            )
            .where(Memory.id == memory_id)
        )
        res = await session.execute(stmt)
        mem = res.scalars().first()
        if not mem:
            return None

        # 1. Inspect evidences
        evidence_items: list[ProvenanceEvidenceItem] = []
        commit_shas: set[str] = set()

        for ev in mem.evidences:
            sym_name = None
            sym_sig = None
            if ev.symbol_id:
                sym = await session.get(CodeEntity, ev.symbol_id)
                if sym:
                    sym_name = sym.qualified_name
                    sym_sig = sym.signature

            if ev.commit_sha:
                commit_shas.add(ev.commit_sha)

            line_range = None
            if ev.line_start is not None:
                line_range = (ev.line_start, ev.line_end or ev.line_start)

            evidence_items.append(
                ProvenanceEvidenceItem(
                    evidence_id=ev.id,
                    source_type=ev.source_type,
                    file_path=ev.file_path,
                    line_range=line_range,
                    symbol_name=sym_name,
                    symbol_signature=sym_sig,
                    commit_sha=ev.commit_sha,
                    verification_status="VERIFIED" if mem.status == "ACTIVE" else mem.status,
                )
            )

        # 2. Inspect version history
        vers_stmt = select(MemoryVersion).where(MemoryVersion.memory_id == memory_id).order_by(MemoryVersion.version)
        vers_res = await session.execute(vers_stmt)
        all_versions = list(vers_res.scalars().all())

        history = []
        for ver in all_versions:
            history.append({
                "version": ver.version,
                "old_state": ver.old_state,
                "new_state": ver.new_state,
                "actor": ver.actor,
                "commit_sha": ver.commit_sha,
                "reason": ver.change_reason,
                "created_at": ver.created_at.isoformat() if ver.created_at else "",
            })
            if ver.commit_sha:
                commit_shas.add(ver.commit_sha)

        # 3. Retrieve associated commits
        commits_data = []
        for csha in commit_shas:
            c_stmt = select(Commit).where(
                Commit.project_id == mem.project_id,
                Commit.commit_sha == csha,
            )
            c_res = await session.execute(c_stmt)
            commit = c_res.scalars().first()
            if commit:
                commits_data.append({
                    "sha": commit.commit_sha,
                    "author": commit.author,
                    "message": commit.message,
                    "branch": commit.branch,
                    "committed_at": commit.committed_at.isoformat(),
                })

        # 4. Synthesize clear explanation of why CortexForge believes this
        why_parts = [
            f"Source authority '{mem.source_type}' with confidence {mem.confidence:.2f}.",
            f"Status is '{mem.status}' across {len(evidence_items)} code evidence items.",
        ]
        if evidence_items:
            first_ev = evidence_items[0]
            if first_ev.symbol_name:
                why_parts.append(f"Grounded directly in symbol '{first_ev.symbol_name}' ({first_ev.file_path}).")
            else:
                why_parts.append(f"Grounded in file '{first_ev.file_path}'.")

        return MemoryProvenanceReport(
            memory_id=mem.id,
            title=mem.title,
            status=mem.status,
            layer=mem.layer,
            source_type=mem.source_type,
            source_authority=mem.confidence,
            confidence=mem.confidence,
            why_believed=" ".join(why_parts),
            created_at=mem.created_at.isoformat() if mem.created_at else "",
            last_verified_at=mem.last_verified_at.isoformat() if mem.last_verified_at else None,
            evidences=evidence_items,
            version_history=history,
            associated_commits=commits_data,
        )

    @classmethod
    async def trace_memory(
        cls, session: AsyncSession, memory_id: str
    ) -> dict[str, Any] | None:
        """Trace full causal provenance graph for a memory formatted as dictionary."""
        report = await cls.get_provenance(session, memory_id)
        if not report:
            return None

        evidences_list = []
        symbols_list = []
        files_list = []
        commits_list = []

        for ev in report.evidences:
            ev_dict = {
                "evidence_id": ev.evidence_id,
                "source_type": ev.source_type,
                "file_path": ev.file_path,
                "line_start": ev.line_range[0] if ev.line_range else None,
                "line_end": ev.line_range[1] if ev.line_range else None,
                "commit_sha": ev.commit_sha,
                "verification_status": ev.verification_status,
            }
            evidences_list.append(ev_dict)
            if ev.file_path and ev.file_path not in files_list:
                files_list.append(ev.file_path)
            if ev.symbol_name:
                symbols_list.append({
                    "name": ev.symbol_name.split(".")[-1],
                    "qualified_name": ev.symbol_name,
                    "file_path": ev.file_path,
                    "signature": ev.symbol_signature,
                })
            if ev.commit_sha and ev.commit_sha not in commits_list:
                commits_list.append(ev.commit_sha)

        for c in report.associated_commits:
            if c["sha"] not in commits_list:
                commits_list.append(c["sha"])

        return {
            "memory_id": report.memory_id,
            "title": report.title,
            "layer": report.layer,
            "memory_type": report.source_type,
            "status": report.status,
            "confidence": report.confidence,
            "why_cortexforge_believes_this": report.why_believed,
            "evidences": evidences_list,
            "symbols": symbols_list,
            "files": files_list,
            "commits": commits_list,
            "versions": report.version_history,
            "tests": report.associated_tests,
        }

