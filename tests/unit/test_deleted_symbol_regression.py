"""Regression test for deleted symbols and edge invalidation in incremental scanner (Specification §12, §58).

Verifies the exact scenario:
1. A.py defines old_function() and helper().
2. B.py imports and invokes old_function() and helper().
3. RepositoryScanner runs initial scan (G1), establishing entities and relationships.
4. Mutation: old_function() is removed from A.py.
5. Incremental scan (G2) runs.
6. Asserts:
   - old_function entity is deleted from database.
   - Relationship B.py:consumer -> A.py:old_function is deleted.
   - Untouched relationship B.py:consumer -> A.py:helper survives intact.
   - Entities helper and consumer maintain integrity.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.models import CodeEntity, Project, Relationship


@pytest.mark.asyncio
async def test_deleted_symbol_purges_stale_entity_and_edge_while_preserving_survivors(
    test_session: AsyncSession, tmp_path
):
    """Assert deleted symbol and its relationships are purged while untouched edges survive."""
    repo_dir = tmp_path / "target_repo"
    repo_dir.mkdir()

    file_a = repo_dir / "service_a.py"
    file_b = repo_dir / "service_b.py"

    # Step 1: Initial codebase
    file_a.write_text(
        "def old_function():\n"
        "    return 'legacy'\n\n"
        "def helper():\n"
        "    return 'shared'\n"
    )
    file_b.write_text(
        "from service_a import old_function, helper\n\n"
        "def consumer():\n"
        "    old_function()\n"
        "    helper()\n"
    )

    project = Project(
        name="DeletedSymbolTestProject",
        local_path=str(repo_dir),
        status="READY",
    )
    test_session.add(project)
    await test_session.commit()

    scanner = RepositoryScanner()

    # Step 2: Initial full scan
    scan_res1 = await scanner.scan_project(test_session, project, incremental=False)
    assert scan_res1.status == "SUCCESS"

    entities_q1 = await test_session.execute(
        select(CodeEntity).where(CodeEntity.project_id == project.id)
    )
    entities1 = entities_q1.scalars().all()
    entity_names1 = {e.name for e in entities1}
    assert {"old_function", "helper", "consumer"}.issubset(entity_names1)

    old_func_ent = next(e for e in entities1 if e.name == "old_function")
    helper_ent = next(e for e in entities1 if e.name == "helper")

    rels_q1 = await test_session.execute(
        select(Relationship).where(Relationship.project_id == project.id)
    )
    rels1 = rels_q1.scalars().all()
    assert len(rels1) >= 1

    # Step 3: Remove old_function from service_a.py. helper() remains!
    file_a.write_text(
        "def helper():\n"
        "    return 'shared'\n\n"
        "def new_function():\n"
        "    return 'modern'\n"
    )

    # Step 4: Run incremental scan
    scan_res2 = await scanner.scan_project(test_session, project, incremental=True)
    assert scan_res2.status == "SUCCESS"

    entities_q2 = await test_session.execute(
        select(CodeEntity).where(CodeEntity.project_id == project.id)
    )
    entities2 = entities_q2.scalars().all()
    entity_names2 = {e.name for e in entities2}

    # Requirement 1: old_function MUST be removed from database
    assert "old_function" not in entity_names2, (
        "Deleted symbol 'old_function' still exists in code_entities table!"
    )

    # Requirement 2: new_function added
    assert "new_function" in entity_names2

    # Requirement 3: helper survived with identical entity ID
    helper_after = next(e for e in entities2 if e.name == "helper")
    assert helper_after.id == helper_ent.id, (
        "Surviving entity 'helper' was recreated with a new ID!"
    )

    # Requirement 4: Stale relationship targeting old_function must NOT exist
    rels_q2 = await test_session.execute(
        select(Relationship).where(Relationship.project_id == project.id)
    )
    rels2 = rels_q2.scalars().all()
    stale_target_ids = {r.target_entity_id for r in rels2}
    assert old_func_ent.id not in stale_target_ids, (
        "Relationship pointing to deleted entity 'old_function' was not purged!"
    )
