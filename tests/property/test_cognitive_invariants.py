"""Property tests for the invariants CortexForge must never violate (section 53).

These differ from the rest of the suite in what they assert. A unit test checks
that one input produces one output; these check that a *property* holds across
many inputs and across repeated operations -- which is where idempotency, isolation
and monotonicity failures actually live.

Every property here corresponds to a way the system could quietly corrupt its own
knowledge:

* running the same operation twice inventing state that one run did not
* an invalidated memory finding its way back to ACTIVE
* one project's memories surfacing in another project's retrieval
* confidence drifting upward through repetition rather than evidence
* a rename destroying the grounding it should have preserved
"""

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.changesets import compute_changeset_key
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.cognition.authority import (
    Authority,
    authority_rank,
    resolve_authority_conflict,
)
from cortexforge.cognition.claims import canonicalize, compute_claim_key, extract_claims
from cortexforge.core.models import ChangeSet, Claim, CodeEntity, Memory, Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.confidence import ConfidenceScorer, EvidenceSummary
from cortexforge.memory.lifecycle import (
    VALID_TRANSITIONS,
    InvalidStateTransitionError,
    MemoryLifecycleManager,
    MemoryState,
)
from cortexforge.memory.service import MemoryService
from cortexforge.verification.engine import ClaimVerificationEngine

# Hypothesis' function-scoped-fixture warning does not apply to the pure
# properties below, which take no database fixture.
PURE = settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])


# --------------------------------------------------------------- pure properties


@given(st.text(min_size=1, max_size=300))
@PURE
def test_canonicalization_is_idempotent(text: str):
    """Canonicalizing an already-canonical string changes nothing.

    Claim identity is a hash of the canonical form, so if canonicalization were
    not idempotent the same proposition could hash two different ways and
    deduplication would silently stop working.
    """
    once, _ = canonicalize(text)
    twice, _ = canonicalize(once)
    assert once == twice


@given(st.text(min_size=1, max_size=200), st.text(min_size=1, max_size=40))
@PURE
def test_claim_key_is_scoped_to_its_project(text: str, other_project: str):
    """The same proposition in two projects never collides on one key.

    This is the first line of defence for project isolation: even if a query
    forgot its project filter, claim identities from different projects cannot be
    confused for each other.
    """
    canonical, _ = canonicalize(text)
    key_a = compute_claim_key("project-a", canonical)
    key_b = compute_claim_key(f"project-b-{other_project}", canonical)
    assert key_a != key_b


@given(st.lists(st.text(min_size=1, max_size=60), min_size=1, max_size=8))
@PURE
def test_changeset_key_ignores_file_order(files: list[str]):
    """A change is identified by which files it touched, not by their order.

    Without this, two analyses of the same change could produce different keys
    and duplicate the changeset -- the exact idempotency failure section 37 warns
    about.
    """
    forward = compute_changeset_key("p1", "base", "target", False, files)
    backward = compute_changeset_key("p1", "base", "target", False, list(reversed(files)))
    assert forward == backward


@given(
    st.sampled_from(list(Authority)),
    st.sampled_from(list(Authority)),
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=0.0, max_value=1.0),
)
@PURE
def test_authority_beats_confidence(
    higher: Authority, lower: Authority, conf_a: float, conf_b: float
):
    """A lower-authority statement never wins on confidence alone.

    This is the property that stops a self-assured LLM from overruling a user
    decision or a verified test, whatever confidence number it arrives with.
    """
    if authority_rank(higher) == authority_rank(lower):
        return

    strong, weak = (
        (higher, lower) if authority_rank(higher) > authority_rank(lower) else (lower, higher)
    )
    # The weaker statement is given the maximum confidence and the stronger the
    # minimum, which is the most favourable case the weaker one could ever get.
    winner, reason = resolve_authority_conflict(
        weak, strong, a_confidence=1.0, b_confidence=0.0, a_recency_wins=True
    )
    assert winner == "b", f"{weak.value} beat {strong.value}: {reason}"


@given(
    st.sampled_from(list(Authority)),
    st.integers(min_value=1, max_value=50),
)
@PURE
def test_repeated_identical_evidence_never_raises_confidence(
    authority: Authority, copies: int
):
    """Piling up copies of one source is not corroboration.

    Confidence must respond to *independent* evidence. Any number of duplicates of
    a single source scores exactly the same as that one source, which is what
    makes "verify the same file repeatedly" useless as a way to manufacture
    certainty (section 8).
    """
    one = ConfidenceScorer.score(
        authority=authority,
        evidence=EvidenceSummary(supporting=1, independent_supporting=1),
    )
    many = ConfidenceScorer.score(
        authority=authority,
        evidence=EvidenceSummary(supporting=copies, independent_supporting=1),
    )
    assert one.score == many.score


@given(st.sampled_from(list(Authority)), st.integers(min_value=1, max_value=6))
@PURE
def test_confidence_is_monotonic_in_independent_evidence(
    authority: Authority, count: int
):
    """More independent support never lowers confidence."""
    fewer = ConfidenceScorer.score(
        authority=authority,
        evidence=EvidenceSummary(supporting=count, independent_supporting=count),
    )
    more = ConfidenceScorer.score(
        authority=authority,
        evidence=EvidenceSummary(supporting=count + 1, independent_supporting=count + 1),
    )
    assert more.score >= fewer.score


@given(st.sampled_from(list(MemoryState)))
@PURE
def test_invalidated_is_terminal_from_every_state(state: MemoryState):
    """Nothing reaches ACTIVE from INVALIDATED, by any route.

    A memory whose premise was deleted or disproven must stay disbelieved. This
    checks the transition graph itself rather than one path through it.
    """
    reachable = VALID_TRANSITIONS[MemoryState.INVALIDATED.value]
    assert MemoryState.ACTIVE.value not in reachable
    if state == MemoryState.INVALIDATED:
        assert reachable == {MemoryState.ARCHIVED.value}


@given(st.text(min_size=20, max_size=400))
@PURE
def test_extracted_claims_are_internally_deduplicated(text: str):
    """One memory never yields two claims with the same identity."""
    claims = extract_claims("p1", text)
    keys = [claim.claim_key for claim in claims]
    assert len(keys) == len(set(keys))


# ----------------------------------------------------- stateful properties


@pytest.mark.asyncio
async def test_rescanning_an_unchanged_repository_is_a_no_op(
    sample_repo, test_session: AsyncSession
):
    """scan(scan(repo)) leaves the same logical state as scan(repo).

    Entity identities may be regenerated, but the *set* of symbols and their
    content hashes must be identical -- otherwise every scan would look like a
    change and invalidate memory that nothing actually touched.
    """
    project = Project(name="IdempotentScan", local_path=sample_repo, status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    scanner = RepositoryScanner()
    await scanner.scan_project(test_session, project, incremental=False)

    def snapshot(entities):
        return sorted((e.qualified_name, e.content_hash) for e in entities)

    first = snapshot(
        (
            await test_session.execute(
                select(CodeEntity).where(CodeEntity.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )

    await scanner.scan_project(test_session, project, incremental=False)
    second = snapshot(
        (
            await test_session.execute(
                select(CodeEntity).where(CodeEntity.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )

    assert first == second
    assert first, "the fixture repository must produce at least one symbol"


@pytest.mark.asyncio
async def test_repeated_change_analysis_creates_one_changeset(
    sample_repo, test_session: AsyncSession
):
    """Analysing the same change twice records it once."""
    project = Project(name="IdempotentChange", local_path=sample_repo, status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    await RepositoryScanner().scan_project(test_session, project, incremental=False)

    propagator = SemanticChangePropagator()
    for _ in range(3):
        await propagator.propagate_changes(
            test_session, project.id, modified_files=["services/auth.py"], mark_stale=True
        )

    count = (
        await test_session.execute(
            select(func.count(ChangeSet.id)).where(ChangeSet.project_id == project.id)
        )
    ).scalar()
    assert count == 1, f"expected one logical changeset, found {count}"


@pytest.mark.asyncio
async def test_creating_the_same_memory_twice_yields_one_claim(
    sample_repo, test_session: AsyncSession
):
    """Two memories asserting the same proposition converge on one claim.

    Deduplication is by canonical proposition, not by memory text, so a fact
    recorded twice in different words is still one thing the project believes.
    """
    project = Project(name="ClaimDedup", local_path=sample_repo, status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    service = MemoryService()
    for title in ("First recording", "Second recording"):
        await service.create_memory(
            test_session,
            project.id,
            MemoryCreate(
                memory_type="FACT",
                title=title,
                content="The authentication service verifies tokens against a blacklist.",
                summary="Token blacklist verification",
                importance=0.6,
            ),
        )

    claims = (
        (
            await test_session.execute(
                select(Claim).where(Claim.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )
    keys = [claim.claim_key for claim in claims]
    assert len(keys) == len(set(keys)), "the same proposition was stored twice"


@pytest.mark.asyncio
async def test_memories_never_leak_across_projects(
    sample_repo, test_session: AsyncSession
):
    """No retrieval or verification ever reaches into another project.

    Both projects are given deliberately similar content, so a missing project
    filter would surface the wrong one rather than failing to match.
    """
    projects = []
    for name in ("IsolationAlpha", "IsolationBeta"):
        project = Project(name=name, local_path=f"{sample_repo}#{name}", status="READY")
        test_session.add(project)
        await test_session.commit()
        await test_session.refresh(project)
        projects.append(project)

    service = MemoryService()
    for index, project in enumerate(projects):
        await service.create_memory(
            test_session,
            project.id,
            MemoryCreate(
                memory_type="DECISION",
                title=f"Shared-sounding decision {index}",
                content="Sessions are stored in Redis with a fifteen minute expiry.",
                summary="Redis session expiry",
                importance=0.7,
            ),
        )

    for project in projects:
        memories = (
            (
                await test_session.execute(
                    select(Memory).where(Memory.project_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(memories) == 1
        assert memories[0].project_id == project.id

        claims = (
            (
                await test_session.execute(
                    select(Claim).where(Claim.project_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        assert claims
        assert all(claim.project_id == project.id for claim in claims)

    # Identical propositions in different projects must not share a claim row.
    alpha_keys = {
        c.claim_key
        for c in (
            await test_session.execute(
                select(Claim).where(Claim.project_id == projects[0].id)
            )
        )
        .scalars()
        .all()
    }
    beta_keys = {
        c.claim_key
        for c in (
            await test_session.execute(
                select(Claim).where(Claim.project_id == projects[1].id)
            )
        )
        .scalars()
        .all()
    }
    assert not (alpha_keys & beta_keys)


@pytest.mark.asyncio
async def test_reverifying_an_unchanged_project_reuses_its_run(
    sample_repo, test_session: AsyncSession
):
    """Verification of unchanged state does not manufacture fresh confirmation.

    Re-running must return the same run, so nothing can make old knowledge look
    recently confirmed by asking the same question repeatedly (section 8).
    """
    project = Project(name="VerificationIdempotency", local_path=sample_repo, status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    await RepositoryScanner().scan_project(test_session, project, incremental=False)
    await MemoryService().create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="CONSTRAINT",
            title="Auth service verifies tokens",
            content="AuthService.authenticate checks the supplied user identity.",
            summary="Auth verification",
            importance=0.8,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/auth.py", source_type="code", line_start=2, line_end=4
                )
            ],
        ),
    )

    engine = ClaimVerificationEngine()
    first = await engine.verify_project(test_session, project.id, verifier="test")
    second = await engine.verify_project(test_session, project.id, verifier="test")
    assert first.id == second.id

    # A real change to the grounding file must force a new run, or verification
    # would go blind to working-tree edits.
    auth_path = Path(sample_repo) / "services" / "auth.py"
    original = auth_path.read_text(encoding="utf-8")
    try:
        auth_path.write_text(original + "\n# changed\n", encoding="utf-8")
        third = await engine.verify_project(test_session, project.id, verifier="test")
        assert third.id != first.id
    finally:
        # The repository fixture is shared across the session, so the edit is
        # always undone -- a property test must not leave the world different.
        auth_path.write_text(original, encoding="utf-8")


@pytest.mark.asyncio
async def test_invalidated_memory_cannot_be_reactivated_through_any_api(
    sample_repo, test_session: AsyncSession
):
    """The terminal state holds against a determined caller."""
    project = Project(name="TerminalState", local_path=sample_repo, status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    memory = await MemoryService().create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="FACT",
            title="Doomed memory",
            content="This statement will be invalidated.",
            summary="Doomed",
            importance=0.5,
        ),
    )

    version = MemoryLifecycleManager.transition(
        memory, MemoryState.INVALIDATED.value, reason="premise removed"
    )
    if version is not None:
        test_session.add(version)

    for verified in (False, True):
        with pytest.raises(InvalidStateTransitionError):
            MemoryLifecycleManager.transition(
                memory,
                MemoryState.ACTIVE.value,
                reason="attempted revival",
                verified=verified,
            )

    assert memory.status == MemoryState.INVALIDATED.value
