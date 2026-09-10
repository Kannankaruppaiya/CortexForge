"""Learning from what worked (specification section 17).

CortexForge already recorded failures in detail. Recording only failures teaches a
system what to avoid and nothing about what to do, so an agent facing a task very
like one that was solved last week starts from scratch.

A ``SuccessEpisode`` records the approach that worked, the task it worked on, the
code it touched, and the tests that passed afterwards -- keyed by a signature over
the normalized approach so that replaying an agent's event stream records one
logical success rather than one per delivery (section 37).

Retrieval of past successes is by task similarity across several signals, not
string overlap: an approach that fixed a connection-pool exhaustion is relevant to
a new pool-starvation task whether or not the two descriptions share words.
"""

import hashlib
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.cognition.claims import canonicalize
from cortexforge.core.models import AgentTask, FailureEpisode, SuccessEpisode, TestRun
from cortexforge.embeddings.provider import EmbeddingProvider, get_embedding_provider
from cortexforge.memory.conflict_resolver import cosine_similarity

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


def success_signature(project_id: str, task_id: str | None, approach: str) -> str:
    """Identity of a success: the project, the task, and the canonical approach.

    Canonicalizing the approach means two recordings of the same fix -- phrased
    slightly differently by two event deliveries -- collapse onto one episode.
    """
    canonical, _ = canonicalize(approach)
    material = f"{project_id}|{task_id or ''}|{canonical}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class SuccessIntelligence:
    """Records and retrieves approaches that are known to have worked."""

    def __init__(self, embedding_provider: EmbeddingProvider | None = None) -> None:
        self.embeddings = embedding_provider or get_embedding_provider()

    async def record_success(
        self,
        session: AsyncSession,
        project_id: str,
        title: str,
        task_context: str,
        approach: str,
        why_it_worked: str | None = None,
        task_id: str | None = None,
        failure_episode_id: str | None = None,
        affected_files: list[str] | None = None,
        affected_symbols: list[str] | None = None,
        test_run_id: str | None = None,
        tests_passed: int = 0,
        commit_sha: str | None = None,
        branch: str | None = None,
    ) -> tuple[SuccessEpisode, bool]:
        """Record a successful approach, returning it and whether it was new.

        Re-recording the same approach for the same task returns the existing
        episode rather than accumulating near-identical rows.
        """
        signature = success_signature(project_id, task_id, approach)

        existing = await session.execute(
            select(SuccessEpisode).where(
                SuccessEpisode.project_id == project_id,
                SuccessEpisode.signature == signature,
            )
        )
        found = existing.scalars().first()
        if found is not None:
            # Later evidence of the same success may arrive with more detail
            # attached; enrich rather than duplicate.
            if test_run_id and not found.test_run_id:
                found.test_run_id = test_run_id
                found.tests_passed = tests_passed
            if commit_sha and not found.commit_sha:
                found.commit_sha = commit_sha
            return found, False

        embedding = await self.embeddings.embed_text(
            f"{title}\n{task_context}\n{approach}"
        )

        episode = SuccessEpisode(
            project_id=project_id,
            task_id=task_id,
            failure_episode_id=failure_episode_id,
            title=title[:255],
            task_context=task_context,
            approach=approach,
            why_it_worked=why_it_worked,
            affected_files=affected_files or [],
            affected_symbols=affected_symbols or [],
            test_run_id=test_run_id,
            tests_passed=tests_passed,
            commit_sha=commit_sha,
            branch=branch,
            signature=signature,
            embedding={"vector": embedding.vector, "model": embedding.model},
        )
        session.add(episode)
        await session.flush()
        return episode, True

    async def record_from_fix(
        self,
        session: AsyncSession,
        project_id: str,
        failure_episode_id: str,
        attempted_fix: str,
        why_it_worked: str | None = None,
        task_id: str | None = None,
        commit_sha: str | None = None,
    ) -> tuple[SuccessEpisode, bool] | None:
        """Turn a successful fix attempt into a retrievable success episode.

        The failure it resolved is carried across, so a future agent hitting the
        same failure signature finds both the problem and the thing that solved it.
        """
        failure = await session.get(FailureEpisode, failure_episode_id)
        if failure is None:
            return None

        return await self.record_success(
            session,
            project_id=project_id,
            title=f"Fix for {failure.error_class}: {failure.error_message[:120]}",
            task_context=failure.error_message,
            approach=attempted_fix,
            why_it_worked=why_it_worked,
            task_id=task_id or failure.task_id,
            failure_episode_id=failure_episode_id,
            affected_files=list(failure.affected_files or []),
            affected_symbols=list(failure.affected_symbols or []),
            commit_sha=commit_sha or failure.commit_sha,
        )

    async def find_similar_successes(
        self,
        session: AsyncSession,
        project_id: str,
        task_text: str,
        target_files: list[str] | None = None,
        failure_signature: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve past successes relevant to a task, with why each was selected.

        Similarity combines several independent signals rather than one, because
        any single one misleads: embeddings alone match on topic while ignoring
        which files a task touches, and file overlap alone ignores what the task is
        trying to do.
        """
        res = await session.execute(
            select(SuccessEpisode)
            .where(SuccessEpisode.project_id == project_id)
            .order_by(SuccessEpisode.created_at.desc())
            .limit(200)
        )
        episodes = list(res.scalars().all())
        if not episodes:
            return []

        query_embedding = await self.embeddings.embed_text(task_text)
        _, query_tokens = canonicalize(task_text)
        query_token_set = set(query_tokens)
        target_set = {f.replace("\\", "/") for f in (target_files or [])}

        scored: list[dict[str, Any]] = []
        for episode in episodes:
            reasons: list[str] = []

            vector = (episode.embedding or {}).get("vector")
            semantic = (
                cosine_similarity(query_embedding.vector, vector) if vector else 0.0
            )
            if semantic > 0.4:
                reasons.append(f"semantically similar task (cosine {semantic:.2f})")

            _, episode_tokens = canonicalize(f"{episode.title} {episode.task_context}")
            shared = query_token_set & set(episode_tokens)
            lexical = len(shared) / max(1, len(query_token_set | set(episode_tokens)))
            if len(shared) >= 2:
                reasons.append(
                    f"shared task vocabulary: {', '.join(sorted(shared)[:4])}"
                )

            episode_files = {
                str(f).replace("\\", "/") for f in (episode.affected_files or [])
            }
            overlap = target_set & episode_files
            file_score = len(overlap) / max(1, len(target_set)) if target_set else 0.0
            if overlap:
                reasons.append(
                    f"touched the same files: {', '.join(sorted(overlap)[:3])}"
                )

            signature_match = 0.0
            if failure_signature and episode.failure_episode_id:
                failure = await session.get(FailureEpisode, episode.failure_episode_id)
                if failure and failure.failure_signature == failure_signature:
                    signature_match = 1.0
                    reasons.append("resolved a failure with this exact signature")

            # Weighted so that an exact failure-signature match dominates: it is
            # the strongest evidence that this approach applies here.
            score = (
                0.30 * semantic
                + 0.20 * lexical
                + 0.20 * file_score
                + 0.30 * signature_match
            )
            if score <= 0.0 or not reasons:
                continue

            scored.append(
                {
                    "id": episode.id,
                    "title": episode.title,
                    "approach": episode.approach,
                    "why_it_worked": episode.why_it_worked,
                    "affected_files": list(episode.affected_files or []),
                    "tests_passed": episode.tests_passed,
                    "commit_sha": episode.commit_sha,
                    "score": round(score, 4),
                    "why_selected": "; ".join(reasons),
                }
            )

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:limit]

    async def record_task_success(
        self,
        session: AsyncSession,
        task: AgentTask,
        approach: str,
        affected_files: list[str] | None = None,
        commit_sha: str | None = None,
    ) -> tuple[SuccessEpisode, bool] | None:
        """Record the successful completion of an agent task.

        Only genuinely successful tasks are recorded. A task that "completed"
        without succeeding teaches nothing worth retrieving later.
        """
        if not task.success:
            return None

        latest_run = await session.execute(
            select(TestRun)
            .where(TestRun.task_id == task.id)
            .order_by(TestRun.created_at.desc())
            .limit(1)
        )
        run = latest_run.scalars().first()

        return await self.record_success(
            session,
            project_id=task.project_id,
            title=f"Completed: {_WHITESPACE.sub(' ', task.task_text)[:180]}",
            task_context=task.task_text,
            approach=approach,
            task_id=task.id,
            affected_files=affected_files or [],
            test_run_id=run.id if run else None,
            tests_passed=run.passed_count if run else 0,
            commit_sha=commit_sha,
        )
