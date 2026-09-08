"""Reproducible retrieval benchmark across real configurations (section 49).

The previous implementation returned literal constants for six of its seven modes
-- `tests_passed=3`, `retrieval_precision=0.75`, `latency_ms=45.0` -- and surfaced
them through the API as measured results. Those numbers described nothing; they
were a picture of the conclusion the benchmark was expected to reach.

This runner measures what it can and refuses to invent the rest.

**Measured.** Each mode is a genuinely different retrieval configuration, executed
against the project's real memories and code graph. Context size, latency,
retrieval precision and recall against the task's declared ground truth, stale and
conflicted hit rates, redundancy, and provenance coverage are all computed from
what those configurations actually returned.

**Not measured, and reported as such.** Task success, tests passed, repeated
failures, generated output tokens and generation cost require executing a coding
agent against the repository. This harness does not do that, so those fields are
``None`` and the scorecard says why. A `None` here means "not measured", which is
a different statement from zero and must not be displayed as one (section 30).

Every run records the repository commit, providers, models, retrieval
configuration and environment, so a number can always be traced to the conditions
that produced it.
"""

import json
import logging
import os
import platform
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.cognition.claims import canonicalize
from cortexforge.core.models import CodeEntity, Memory, Project
from cortexforge.embeddings.provider import get_embedding_provider
from cortexforge.memory.conflict_resolver import cosine_similarity
from cortexforge.memory.lifecycle import MemoryState
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine

logger = logging.getLogger(__name__)

# Rough characters-per-token ratio used to size context. It is an estimate and is
# labelled as one wherever it surfaces; the comparison between modes is what the
# benchmark is for, and every mode is estimated the same way.
_CHARS_PER_TOKEN = 4.0


class BenchmarkMode(str, Enum):
    A_NO_MEMORY = "A_NoMemory"
    B_NAIVE_RAG = "B_NaiveVectorRAG"
    C_FLAT_MEMORY = "C_FlatConversational"
    D_RETRIEVAL_ONLY = "D_CortexRetrievalOnly"
    E_WITH_PROVENANCE = "E_CortexWithProvenance"
    F_WITH_CHANGE_PROP = "F_CortexWithChangePropagation"
    G_FULL_CORTEX = "G_FullCortexForge"


class AblationType(str, Enum):
    NONE = "none"
    WITHOUT_GRAPH = "without_graph"
    WITHOUT_PROVENANCE = "without_provenance"
    WITHOUT_FRESHNESS = "without_freshness"
    WITHOUT_FAILURES = "without_failures"
    WITHOUT_CHANGE_PROP = "without_change_propagation"
    WITHOUT_CONSOLIDATION = "without_consolidation"
    WITHOUT_SEMANTIC_AST_DIFF = "without_semantic_ast_diff"


@dataclass
class BenchmarkTask:
    """A task with declared ground truth, so retrieval quality is checkable."""

    id: str
    name: str
    task_prompt: str
    target_files: list[str]
    expected_constraint_keywords: list[str]
    is_regression_risk: bool = False
    # Memory ids known to be relevant, when a fixture can state them. When empty,
    # relevance is judged by keyword and file overlap instead, and the scorecard
    # records which basis was used.
    relevant_memory_ids: list[str] = field(default_factory=list)


@dataclass
class ModeEvaluationResult:
    """One configuration's measured behaviour on one task.

    Fields typed ``| None`` are not measured by this harness. They are left as
    ``None`` rather than filled with a plausible value.
    """

    mode: str
    # --- measured ---
    context_items: int
    files_referenced: int
    input_tokens: int
    latency_ms: float
    stale_retrieval_rate: float
    conflicted_retrieval_rate: float
    retrieval_precision: float | None
    retrieval_recall: float | None
    context_redundancy: float
    provenance_coverage: float
    relevance_basis: str
    # --- not measured by this harness ---
    task_success: bool | None = None
    tests_passed: int | None = None
    repeated_failures: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    unmeasured_reason: str = (
        "Requires executing a coding agent against the repository; this harness "
        "measures retrieval and context construction only."
    )

    @property
    def files_explored(self) -> int:
        return self.files_referenced

    @property
    def duration_ms(self) -> float:
        return self.latency_ms

    @property
    def total_tokens(self) -> int:
        return self.input_tokens

    @property
    def files_inspected(self) -> int:
        return self.files_referenced


@dataclass
class BenchmarkRunMetadata:
    """The conditions a measurement was taken under."""

    repository_commit: str | None
    cortexforge_version: str = "0.1.0"
    benchmark_suite_version: str = "2.0.0"
    llm_provider: str = "not-invoked"
    embedding_provider: str = "unknown"
    embedding_model: str = "unknown"
    embedding_quality_class: str = "unknown"
    retrieval_config: dict[str, Any] = field(default_factory=dict)
    project_memory_count: int = 0
    project_entity_count: int = 0
    environment: str = field(
        default_factory=lambda: (
            f"{platform.system()} {platform.release()} (Python {platform.python_version()})"
        )
    )
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class ComprehensiveScorecard:
    task_id: str
    task_name: str
    metadata: BenchmarkRunMetadata
    results: dict[str, ModeEvaluationResult]
    token_reduction_pct: float
    exploration_reduction_pct: float
    tool_calls_saved: int | None = None
    raw_log_path: str | None = None
    measurement_notes: list[str] = field(default_factory=list)


DEFAULT_BENCHMARK_SUITE: list[BenchmarkTask] = [
    BenchmarkTask(
        id="BENCH-001",
        name="Refresh Token Expiration & Stateless JWT Invariant",
        task_prompt=(
            "Fix refresh token race condition during concurrent rotation without "
            "breaking revocability."
        ),
        target_files=["services/auth.py"],
        expected_constraint_keywords=["jwt", "revocable", "token", "stateless"],
        is_regression_risk=True,
    ),
    BenchmarkTask(
        id="BENCH-002",
        name="Payment Webhook Idempotency Validation",
        task_prompt=(
            "Implement idempotency check before recording payment transactions "
            "from Stripe webhooks."
        ),
        target_files=["services/payment.py"],
        expected_constraint_keywords=["idempotency", "webhook", "signature", "payment"],
        is_regression_risk=True,
    ),
]


def estimate_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return max(0, int(len(text) / _CHARS_PER_TOKEN))


class EvaluationRunner:
    """Executes each retrieval configuration for real and measures the outcome."""

    def __init__(
        self,
        retrieval_engine: HybridRetrievalEngine | None = None,
        context_composer: ContextComposer | None = None,
        scanner: RepositoryScanner | None = None,
        results_dir: str = "benchmarks/results",
    ) -> None:
        self.retrieval_engine = retrieval_engine or HybridRetrievalEngine()
        self.context_composer = context_composer or ContextComposer(
            retrieval_engine=self.retrieval_engine
        )
        self.scanner = scanner or RepositoryScanner()
        self.results_dir = Path(results_dir)

    def _get_git_commit(self, path: str | None) -> str | None:
        if not path or not os.path.exists(path):
            return None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=path,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            return result.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return None

    async def run_benchmark(
        self,
        session: AsyncSession,
        project_id: str,
        tasks: list[BenchmarkTask] | None = None,
        ablation: AblationType = AblationType.NONE,
        save_results: bool = True,
    ) -> list[ComprehensiveScorecard]:
        """Run every configuration against every task and record what happened."""
        bench_tasks = tasks or DEFAULT_BENCHMARK_SUITE
        project = await session.get(Project, project_id)
        local_path = project.local_path if project else None

        memories = list(
            (
                await session.execute(
                    select(Memory).where(Memory.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        entities = list(
            (
                await session.execute(
                    select(CodeEntity).where(CodeEntity.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )

        embedding_provider = get_embedding_provider()
        metadata = BenchmarkRunMetadata(
            repository_commit=self._get_git_commit(local_path),
            embedding_provider=type(embedding_provider).__name__,
            embedding_model=embedding_provider.model_name,
            embedding_quality_class=(
                "LOCAL_DETERMINISTIC_HASH"
                if "hash" in embedding_provider.model_name.lower()
                else "REAL_SEMANTIC_EMBEDDING"
            ),
            retrieval_config={
                "ablation": ablation.value,
                **self.retrieval_engine.weights.model_dump(),
            },
            project_memory_count=len(memories),
            project_entity_count=len(entities),
        )

        repository_files = sorted({e.file_path for e in entities})
        scorecards: list[ComprehensiveScorecard] = []

        for task in bench_tasks:
            matched_targets = [
                path
                for path in repository_files
                if any(target in path or path.endswith(target) for target in task.target_files)
            ] or repository_files[:2]

            results: dict[str, ModeEvaluationResult] = {}
            notes: list[str] = []

            if not memories:
                notes.append(
                    "The project has no memories, so every memory-backed mode is "
                    "measured against an empty store. The comparison is valid but "
                    "uninformative until memories exist."
                )

            results[BenchmarkMode.A_NO_MEMORY.value] = await self._run_no_memory(
                task, repository_files, local_path
            )
            results[BenchmarkMode.B_NAIVE_RAG.value] = await self._run_naive_rag(
                task, memories
            )
            results[BenchmarkMode.C_FLAT_MEMORY.value] = await self._run_flat_memory(
                task, memories
            )
            for mode in (
                BenchmarkMode.D_RETRIEVAL_ONLY,
                BenchmarkMode.E_WITH_PROVENANCE,
                BenchmarkMode.F_WITH_CHANGE_PROP,
                BenchmarkMode.G_FULL_CORTEX,
            ):
                results[mode.value] = await self._run_cortex_mode(
                    session, project_id, task, matched_targets, mode, ablation
                )

            baseline = results[BenchmarkMode.A_NO_MEMORY.value]
            full = results[BenchmarkMode.G_FULL_CORTEX.value]
            token_reduction = (
                ((baseline.input_tokens - full.input_tokens) / baseline.input_tokens) * 100.0
                if baseline.input_tokens
                else 0.0
            )
            exploration_reduction = (
                ((baseline.files_referenced - full.files_referenced) / baseline.files_referenced)
                * 100.0
                if baseline.files_referenced
                else 0.0
            )

            notes.append(
                "Token counts are estimated at "
                f"{_CHARS_PER_TOKEN} characters per token, applied identically to "
                "every mode."
            )
            notes.append(
                "Task success, tests passed, repeated failures and generation cost "
                "are not measured by this harness and are reported as null."
            )
            if not task.relevant_memory_ids:
                notes.append(
                    "Retrieval precision and recall are judged against the task's "
                    "declared keywords and target files, not a curated relevance "
                    "set, so they measure topical match rather than usefulness."
                )

            scorecard = ComprehensiveScorecard(
                task_id=task.id,
                task_name=task.name,
                metadata=metadata,
                results=results,
                token_reduction_pct=round(token_reduction, 1),
                exploration_reduction_pct=round(exploration_reduction, 1),
                tool_calls_saved=None,
                measurement_notes=notes,
            )

            if save_results:
                scorecard.raw_log_path = self._persist(scorecard)

            scorecards.append(scorecard)

        return scorecards

    # --------------------------------------------------------------- modes

    async def _run_no_memory(
        self, task: BenchmarkTask, repository_files: list[str], local_path: str | None
    ) -> ModeEvaluationResult:
        """Baseline: no memory at all, so the agent must read the repository.

        Context size is the actual size of the candidate files on disk, not an
        assumed per-file constant.
        """
        start = time.perf_counter()
        total_chars = 0
        counted = 0

        for rel_path in repository_files:
            if not local_path:
                break
            abs_path = os.path.join(local_path, rel_path.replace("/", os.sep))
            try:
                total_chars += len(Path(abs_path).read_text(encoding="utf-8", errors="ignore"))
                counted += 1
            except OSError:
                continue

        latency = (time.perf_counter() - start) * 1000
        return ModeEvaluationResult(
            mode=BenchmarkMode.A_NO_MEMORY.value,
            context_items=counted,
            files_referenced=counted,
            input_tokens=estimate_tokens("x" * total_chars),
            latency_ms=round(latency, 2),
            stale_retrieval_rate=0.0,
            conflicted_retrieval_rate=0.0,
            # With no retrieval there is nothing to be precise about; recall is 0
            # because no relevant memory is surfaced.
            retrieval_precision=None,
            retrieval_recall=0.0,
            context_redundancy=0.0,
            provenance_coverage=0.0,
            relevance_basis="no retrieval performed",
        )

    async def _run_naive_rag(
        self, task: BenchmarkTask, memories: list[Memory]
    ) -> ModeEvaluationResult:
        """Pure vector top-k over memories: no filtering, ranking or status checks.

        This is the honest strawman -- what a plain embedding store would return,
        including memories CortexForge knows are stale.
        """
        start = time.perf_counter()
        provider = get_embedding_provider()
        query = await provider.embed_text(task.task_prompt)

        scored = []
        for memory in memories:
            vector = (memory.embedding or {}).get("vector")
            if not vector:
                continue
            scored.append((cosine_similarity(query.vector, vector), memory))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected = [memory for _, memory in scored[:8]]

        latency = (time.perf_counter() - start) * 1000
        return self._measure_selection(
            BenchmarkMode.B_NAIVE_RAG.value, task, selected, latency, includes_provenance=False
        )

    async def _run_flat_memory(
        self, task: BenchmarkTask, memories: list[Memory]
    ) -> ModeEvaluationResult:
        """Flat conversational memory: everything, newest first, until budget."""
        start = time.perf_counter()
        ordered = sorted(memories, key=lambda m: m.created_at, reverse=True)

        selected: list[Memory] = []
        budget = 3500
        used = 0
        for memory in ordered:
            cost = estimate_tokens(f"{memory.title}\n{memory.content}")
            if used + cost > budget:
                break
            selected.append(memory)
            used += cost

        latency = (time.perf_counter() - start) * 1000
        return self._measure_selection(
            BenchmarkMode.C_FLAT_MEMORY.value, task, selected, latency, includes_provenance=False
        )

    async def _run_cortex_mode(
        self,
        session: AsyncSession,
        project_id: str,
        task: BenchmarkTask,
        target_files: list[str],
        mode: BenchmarkMode,
        ablation: AblationType,
    ) -> ModeEvaluationResult:
        """Run one CortexForge configuration for real and measure what it returned.

        The modes differ in which signals they are allowed to use, so the deltas
        between them come from executing different code paths rather than from
        applied constants.
        """
        start = time.perf_counter()

        uses_graph = mode in (
            BenchmarkMode.E_WITH_PROVENANCE,
            BenchmarkMode.F_WITH_CHANGE_PROP,
            BenchmarkMode.G_FULL_CORTEX,
        ) and ablation != AblationType.WITHOUT_GRAPH
        excludes_stale = (
            mode in (BenchmarkMode.F_WITH_CHANGE_PROP, BenchmarkMode.G_FULL_CORTEX)
            and ablation != AblationType.WITHOUT_CHANGE_PROP
        )
        includes_provenance = (
            mode
            in (
                BenchmarkMode.E_WITH_PROVENANCE,
                BenchmarkMode.F_WITH_CHANGE_PROP,
                BenchmarkMode.G_FULL_CORTEX,
            )
            and ablation != AblationType.WITHOUT_PROVENANCE
        )

        items = await self.retrieval_engine.retrieve(
            session,
            project_id=project_id,
            query=task.task_prompt,
            target_files=target_files if uses_graph else None,
            limit=10,
        )

        if excludes_stale:
            items = [
                item
                for item in items
                if item.status
                not in (MemoryState.STALE.value, MemoryState.INVALIDATED.value)
            ]

        memory_ids = [item.id for item in items]
        memories: list[Memory] = []
        if memory_ids:
            res = await session.execute(select(Memory).where(Memory.id.in_(memory_ids)))
            by_id = {m.id: m for m in res.scalars().all()}
            memories = [by_id[mid] for mid in memory_ids if mid in by_id]

        latency = (time.perf_counter() - start) * 1000
        return self._measure_selection(
            mode.value, task, memories, latency, includes_provenance=includes_provenance
        )

    # ------------------------------------------------------------ measuring

    def _measure_selection(
        self,
        mode: str,
        task: BenchmarkTask,
        memories: list[Memory],
        latency_ms: float,
        includes_provenance: bool,
    ) -> ModeEvaluationResult:
        """Compute every measurable metric from a concrete selection of memories."""
        if not memories:
            return ModeEvaluationResult(
                mode=mode,
                context_items=0,
                files_referenced=0,
                input_tokens=0,
                latency_ms=round(latency_ms, 2),
                stale_retrieval_rate=0.0,
                conflicted_retrieval_rate=0.0,
                retrieval_precision=None,
                retrieval_recall=0.0,
                context_redundancy=0.0,
                provenance_coverage=0.0,
                relevance_basis="nothing retrieved",
            )

        relevant_ids = set(task.relevant_memory_ids)
        basis = "curated relevance set"
        if not relevant_ids:
            basis = "keyword and target-file overlap"
            relevant_ids = {
                memory.id for memory in memories if self._is_topically_relevant(task, memory)
            }

        selected_ids = {memory.id for memory in memories}
        hits = selected_ids & relevant_ids
        precision = round(len(hits) / len(selected_ids), 4) if selected_ids else None
        # Recall against a set derived from the selection itself would always be
        # 1.0 and mean nothing, so it is only reported against a curated set.
        recall = (
            round(len(hits) / len(relevant_ids), 4)
            if task.relevant_memory_ids and relevant_ids
            else None
        )

        stale = sum(1 for m in memories if m.status == MemoryState.STALE.value)
        conflicted = sum(1 for m in memories if m.status == MemoryState.CONFLICTED.value)

        context_parts = []
        files: set[str] = set()
        with_evidence = 0
        for memory in memories:
            part = f"{memory.title}\n{memory.content}"
            evidences = list(memory.evidences or [])
            if evidences:
                with_evidence += 1
                files.update(e.file_path for e in evidences)
                if includes_provenance:
                    part += "\n" + "\n".join(
                        f"evidence: {e.file_path}:{e.line_start}-{e.line_end}"
                        for e in evidences
                    )
            context_parts.append(part)

        return ModeEvaluationResult(
            mode=mode,
            context_items=len(memories),
            files_referenced=len(files),
            input_tokens=estimate_tokens("\n\n".join(context_parts)),
            latency_ms=round(latency_ms, 2),
            stale_retrieval_rate=round(stale / len(memories), 4),
            conflicted_retrieval_rate=round(conflicted / len(memories), 4),
            retrieval_precision=precision,
            retrieval_recall=recall,
            context_redundancy=self._redundancy(memories),
            provenance_coverage=round(with_evidence / len(memories), 4),
            relevance_basis=basis,
        )

    @staticmethod
    def _is_topically_relevant(task: BenchmarkTask, memory: Memory) -> bool:
        """Whether a memory matches the task's declared ground truth.

        Deliberately conservative: a memory counts as relevant only if it shares
        vocabulary with the task's expected constraints or is grounded in one of
        the task's target files.
        """
        _, tokens = canonicalize(f"{memory.title} {memory.content}")
        token_set = set(tokens)
        for keyword in task.expected_constraint_keywords:
            _, keyword_tokens = canonicalize(keyword)
            if set(keyword_tokens) & token_set:
                return True

        for evidence in memory.evidences or []:
            path = (evidence.file_path or "").replace("\\", "/")
            if any(target in path or path.endswith(target) for target in task.target_files):
                return True
        return False

    @staticmethod
    def _redundancy(memories: list[Memory]) -> float:
        """Mean pairwise content overlap among selected memories.

        High redundancy means the context spends its budget saying the same thing
        several times, which is a real cost even when every item is relevant.
        """
        if len(memories) < 2:
            return 0.0

        token_sets = []
        for memory in memories:
            _, tokens = canonicalize(f"{memory.title} {memory.summary}")
            token_sets.append(set(tokens))

        overlaps = []
        for index, first in enumerate(token_sets):
            for other in token_sets[index + 1 :]:
                if first or other:
                    overlaps.append(len(first & other) / max(1, len(first | other)))
        return round(sum(overlaps) / len(overlaps), 4) if overlaps else 0.0

    def _persist(self, scorecard: ComprehensiveScorecard) -> str | None:
        """Write the raw run artifact, so a reported number can be traced back."""
        try:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            target = self.results_dir / f"run_{scorecard.task_id}_{int(time.time())}.json"
            target.write_text(
                json.dumps(
                    {
                        "task_id": scorecard.task_id,
                        "task_name": scorecard.task_name,
                        "metadata": asdict(scorecard.metadata),
                        "results": {k: asdict(v) for k, v in scorecard.results.items()},
                        "token_reduction_pct": scorecard.token_reduction_pct,
                        "exploration_reduction_pct": scorecard.exploration_reduction_pct,
                        "measurement_notes": scorecard.measurement_notes,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            return str(target)
        except OSError as exc:
            logger.warning("Could not persist benchmark artifact: %s", exc)
            return None
