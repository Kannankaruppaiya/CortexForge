"""Cognitive snapshots and replay (specification sections 35 and 36).

A snapshot must answer "what did CortexForge believe at commit X?". Counts cannot
answer that -- knowing there were seventeen active memories tells you nothing about
which seventeen, at what confidence, or on what evidence. The previous
implementation stored only counts, and its "replay" composed context from the
*current* memory table while presenting the result as historical, which is a
stronger claim than the data supports.

A snapshot now records the believed set itself: every memory with its version,
status and confidence; every claim with its verification outcome; the architecture
rules in force; the policy, retrieval and embedding versions that defined what
"verified" meant at the time; and a hash over all of it.

Replay reconstructs belief *from the snapshot*, and says plainly when no snapshot
exists for a commit rather than substituting today's answer for that day's.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.cognition.epistemics import ClaimStatus
from cortexforge.core.models import (
    ArchitectureRule,
    Claim,
    CognitiveSnapshot,
    Memory,
    Project,
    VerificationPolicy,
    VerificationRun,
)
from cortexforge.memory.lifecycle import MemoryState
from cortexforge.retrieval.composer import (
    PROFILE_BUDGETS,
    ContextComposer,
    enforce_token_budget,
)

logger = logging.getLogger(__name__)


class CognitiveSnapshotEngine:
    """Captures immutable cognitive state and replays it faithfully."""

    @classmethod
    async def take_snapshot(
        cls,
        session: AsyncSession,
        project_id: str,
        commit_sha: str,
        retrieval_version: str = "v2",
        embedding_version: str = "1.0.0",
        embedding_model: str | None = None,
        branch: str | None = None,
        workspace: str | None = None,
    ) -> CognitiveSnapshot:
        """Record what the project believes, in full, at ``commit_sha``."""
        # Serialize concurrent snapshot operations per project via parent row lock
        try:
            await session.execute(
                select(Project.id).where(Project.id == project_id).with_for_update()
            )
        except Exception as exc:
            logger.debug(
                "Parent row lock not acquired (dialect may not support FOR UPDATE): %s",
                exc,
            )

        memories = list(
            (
                await session.execute(
                    select(Memory)
                    .where(Memory.project_id == project_id)
                    .order_by(Memory.created_at)
                )
            )
            .scalars()
            .all()
        )

        claims = list(
            (
                await session.execute(
                    select(Claim)
                    .where(Claim.project_id == project_id)
                    .order_by(Claim.created_at)
                )
            )
            .scalars()
            .all()
        )

        rules = list(
            (
                await session.execute(
                    select(ArchitectureRule).where(
                        ArchitectureRule.project_id == project_id
                    )
                )
            )
            .scalars()
            .all()
        )

        policies = list(
            (
                await session.execute(
                    select(VerificationPolicy).where(
                        VerificationPolicy.project_id == project_id,
                        VerificationPolicy.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )

        # Only believed memories are counted as active belief; candidates and
        # review-pending proposals are recorded but are explicitly not truth.
        memory_versions = [
            {
                "memory_id": m.id,
                "version": m.version,
                "status": m.status,
                "layer": m.layer,
                "memory_type": m.memory_type,
                "authority": m.authority,
                "confidence": round(m.confidence, 4),
                "title": m.title,
            }
            for m in memories
        ]
        claim_states = [
            {
                "claim_id": c.id,
                "memory_id": c.memory_id,
                "claim_key": c.claim_key,
                "status": c.status,
                "last_outcome": c.last_outcome,
                "confidence": round(c.confidence, 4),
                "authority": c.authority,
            }
            for c in claims
        ]
        rule_versions = [
            {
                "rule_id": r.id,
                "rule_name": r.rule_name,
                "version": r.version,
                "modality": r.modality,
                "severity": r.severity,
                "authority": r.authority,
                "enforcement_status": r.enforcement_status,
            }
            for r in rules
        ]

        latest_run = (
            (
                await session.execute(
                    select(VerificationRun)
                    .where(VerificationRun.project_id == project_id)
                    .order_by(VerificationRun.started_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

        verification_state = (
            {
                "run_id": latest_run.id,
                "commit_sha": latest_run.commit_sha,
                "claims_evaluated": latest_run.claims_evaluated,
                "verified": latest_run.verified_count,
                "partially_verified": latest_run.partially_verified_count,
                "failed": latest_run.failed_count,
                "conflicted": latest_run.conflicted_count,
                "unknown": latest_run.unknown_count,
                "not_applicable": latest_run.not_applicable_count,
            }
            if latest_run is not None
            else {
                "run_id": None,
                "note": "no verification has been run for this project",
            }
        )

        max_gen = (
            await session.execute(
                select(
                    func.coalesce(func.max(CognitiveSnapshot.cognitive_generation), 0)
                ).where(CognitiveSnapshot.project_id == project_id)
            )
        ).scalar() or 0
        generation = int(max_gen) + 1

        counts = {state: 0 for state in (m.status for m in memories)}
        for memory in memories:
            counts[memory.status] = counts.get(memory.status, 0) + 1

        snapshot = CognitiveSnapshot(
            project_id=project_id,
            commit_sha=commit_sha,
            cognitive_generation=generation,
            memory_generation=generation,
            graph_generation=generation,
            index_generation=generation,
            active_memories_count=counts.get(MemoryState.ACTIVE.value, 0),
            stale_memories_count=counts.get(MemoryState.STALE.value, 0),
            conflicted_memories_count=counts.get(MemoryState.CONFLICTED.value, 0),
            retrieval_version=retrieval_version,
            embedding_version=embedding_version,
            embedding_model=embedding_model,
            branch=branch,
            workspace=workspace,
            memory_versions=memory_versions,
            claim_states=claim_states,
            architecture_rule_versions=rule_versions,
            verification_state=verification_state,
            policy_versions={p.name: p.version for p in policies},
            state_hash=cls.compute_state_hash(
                memory_versions, claim_states, rule_versions
            ),
            created_at=datetime.now(UTC),
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    @staticmethod
    def compute_state_hash(
        memory_versions: list[dict[str, Any]],
        claim_states: list[dict[str, Any]],
        rule_versions: list[dict[str, Any]],
    ) -> str:
        """Hash of the believed set, so two snapshots of one state are comparable.

        Only the fields that constitute belief are hashed -- ids, versions, statuses
        and confidences -- deliberately excluding timestamps, so that snapshotting
        an unchanged project twice yields the same hash.
        """
        material = {
            "memories": sorted(
                (
                    (m["memory_id"], m["version"], m["status"], m["confidence"])
                    for m in memory_versions
                ),
            ),
            "claims": sorted(
                ((c["claim_id"], c["status"], c["confidence"]) for c in claim_states),
            ),
            "rules": sorted((r["rule_id"], r["version"]) for r in rule_versions),
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @classmethod
    async def get_snapshot_at_commit(
        cls, session: AsyncSession, project_id: str, commit_sha: str
    ) -> CognitiveSnapshot | None:
        """The snapshot recorded for a project at ``commit_sha``, if any."""
        res = await session.execute(
            select(CognitiveSnapshot)
            .where(
                CognitiveSnapshot.project_id == project_id,
                CognitiveSnapshot.commit_sha == commit_sha,
            )
            .order_by(CognitiveSnapshot.created_at.desc())
        )
        return res.scalars().first()

    @classmethod
    async def list_snapshots(
        cls, session: AsyncSession, project_id: str
    ) -> list[CognitiveSnapshot]:
        """All snapshots for a project, newest first."""
        res = await session.execute(
            select(CognitiveSnapshot)
            .where(CognitiveSnapshot.project_id == project_id)
            .order_by(CognitiveSnapshot.created_at.desc())
        )
        return list(res.scalars().all())

    @classmethod
    async def replay_state_at_commit(
        cls,
        session: AsyncSession,
        project_id: str,
        commit_sha: str,
        task_text: str = "Project cognitive state audit",
        profile: str = "medium",
    ) -> dict[str, Any]:
        """Reconstruct what was believed at a commit, from the snapshot taken then.

        Two things this deliberately does not do: it does not fabricate a snapshot
        for a commit that was never snapshotted, and it does not fall back to
        present-day state while calling the result historical. When no snapshot
        exists the reply says so, and the caller gets ``replay_available: False``
        rather than a confident answer about a moment nobody recorded (section 30).
        """
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} does not exist.")

        snapshot = await cls.get_snapshot_at_commit(session, project_id, commit_sha)
        if snapshot is None:
            available = await cls.list_snapshots(session, project_id)
            return {
                "project_id": project_id,
                "commit_sha": commit_sha,
                "replay_available": False,
                "reason": (
                    f"No cognitive snapshot was recorded at commit {commit_sha}. "
                    "What CortexForge believed at that moment was not captured and "
                    "cannot be reconstructed from present-day state."
                ),
                "available_commits": [s.commit_sha for s in available[:20]],
                "replay_timestamp": datetime.now(UTC).isoformat(),
            }

        believed = [
            entry
            for entry in (snapshot.memory_versions or [])
            if entry.get("status") == MemoryState.ACTIVE.value
        ]
        withheld = [
            entry
            for entry in (snapshot.memory_versions or [])
            if entry.get("status")
            in (
                MemoryState.STALE.value,
                MemoryState.CONFLICTED.value,
                MemoryState.INVALIDATED.value,
                MemoryState.SUPERSEDED.value,
            )
        ]
        unresolved_claims = [
            entry
            for entry in (snapshot.claim_states or [])
            if entry.get("status")
            in (ClaimStatus.UNKNOWN.value, ClaimStatus.CONFLICTED.value)
        ]

        # Deterministically score and select candidate memories based on task_text and profile
        task_tokens = set(task_text.lower().split())
        scored_candidates = []
        for mem in believed:
            title_tokens = set(mem.get("title", "").lower().split())
            overlap = len(task_tokens & title_tokens)
            conf = float(mem.get("confidence", 1.0))
            score = overlap * 10.0 + conf
            scored_candidates.append((score, mem))

        # Sort descending by score, tie-break on memory_id for pure determinism
        scored_candidates.sort(key=lambda x: (-x[0], str(x[1].get("memory_id", ""))))

        budget = PROFILE_BUDGETS.get(profile.lower(), 3500)
        lines = [
            f"# Replay Cognitive State: Commit {commit_sha[:8]}",
            f"- **Project**: `{project.name}`",
            f"- **Task**: {task_text}",
            f"- **Profile**: {profile.lower()} (Budget: {budget} tokens)",
            f"- **Snapshot State Hash**: `{snapshot.state_hash}`",
            "",
            "## Believed Architectural Knowledge (As Of Revision)",
        ]

        selected_memories = []
        for score, mem in scored_candidates:
            selected_memories.append(mem)
            lines.append(
                f"- **[{mem.get('memory_type', 'FACT')}] {mem.get('title', '')}** (Layer: {mem.get('layer', 'L1')}, Conf: {mem.get('confidence', 1.0):.2f})"
            )
            # Stop adding to markdown if conservative word count reaches budget
            if sum(len(line.split()) for line in lines) * 1.3 > budget:
                break

        lines.append("")
        lines.append("<!-- END REPLAY CONTEXT -->")
        raw_markdown = "\n".join(lines)
        composed_markdown, _ = enforce_token_budget(raw_markdown, budget)

        # Compute deterministic replay hash
        replay_fingerprint = {
            "snapshot_state_hash": snapshot.state_hash,
            "commit_sha": commit_sha,
            "task_text": task_text.strip(),
            "profile": profile.lower(),
            "selected_ids": [m["memory_id"] for m in selected_memories],
            "composed_markdown": composed_markdown,
        }
        replay_hash = hashlib.sha256(
            json.dumps(replay_fingerprint, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

        return {
            "project_id": project_id,
            "commit_sha": commit_sha,
            "replay_available": True,
            "historical_reference_mode": "SNAPSHOT_REFERENCE",
            "task_text": task_text,
            "profile": profile,
            "snapshot_id": snapshot.id,
            "snapshot_generation": snapshot.cognitive_generation,
            "state_hash": snapshot.state_hash,
            "deterministic_replay_hash": replay_hash,
            "composed_context_markdown": composed_markdown,
            "candidate_memories": believed,
            "selected_memories": selected_memories,
            "retrieval_version": snapshot.retrieval_version,
            "embedding_version": snapshot.embedding_version,
            "parser_version": "treesitter-1.0",
            "policy_versions": snapshot.policy_versions,
            "active_memories_count": snapshot.active_memories_count,
            "believed_memories": believed,
            "withheld_memories": withheld,
            "unresolved_claims": unresolved_claims,
            "architecture_rules": snapshot.architecture_rule_versions,
            "verification_state": snapshot.verification_state,
            "replay_timestamp": datetime.now(UTC).isoformat(),
        }

    @classmethod
    async def compose_current_context(
        cls,
        session: AsyncSession,
        project_id: str,
        task_text: str,
        profile: str = "medium",
    ) -> dict[str, Any]:
        """Compose context from *present* state, labelled as such.

        Kept separate from replay so that "what do you believe now" and "what did
        you believe then" can never be confused for one another at a call site.
        """
        composer = ContextComposer()
        composed = await composer.build_context(
            session=session, project_id=project_id, task_text=task_text, profile=profile
        )
        return {
            "project_id": project_id,
            "as_of": "current",
            "context": str(composed),
            "selected_memories": composed.selected_memories,
            "explainability_report": composed.explainability_report,
        }
