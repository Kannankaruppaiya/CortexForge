"""Unit tests for GraphService project architecture synthesis and entity data contracts."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import CodeEntity, Project, Relationship
from cortexforge.graph.service import GraphService


@pytest.mark.asyncio
async def test_module_with_standard_four_types(test_session: AsyncSession):
    """1. Module containing only class, function, interface, and model entities."""
    project = Project(name="StandardProject", local_path="/tmp/test_project", status="READY")
    test_session.add(project)
    await test_session.commit()

    entities = [
        CodeEntity(
            project_id=project.id,
            entity_type="class",
            name="AuthService",
            qualified_name="src/auth/service.py:AuthService",
            file_path="src/auth/service.py",
            start_line=10,
            end_line=50,
            content_hash="hash1",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="function",
            name="verify_token",
            qualified_name="src/auth/service.py:verify_token",
            file_path="src/auth/service.py",
            start_line=52,
            end_line=70,
            content_hash="hash2",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="interface",
            name="TokenValidator",
            qualified_name="src/auth/types.ts:TokenValidator",
            file_path="src/auth/types.ts",
            start_line=5,
            end_line=20,
            content_hash="hash3",
            language="typescript",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="model",
            name="UserPayload",
            qualified_name="src/auth/models.go:UserPayload",
            file_path="src/auth/models.go",
            start_line=1,
            end_line=15,
            content_hash="hash4",
            language="go",
        ),
    ]
    test_session.add_all(entities)
    await test_session.commit()

    graph_service = GraphService()
    arch = await graph_service.get_project_architecture(test_session, project.id)

    assert arch is not None
    assert len(arch.modules) == 1
    mod = arch.modules[0]
    assert mod.module_path == "src/auth"
    assert mod.entity_count == 4

    comp_types = [c.entity_type for c in mod.top_level_components]
    assert "class" in comp_types
    assert "function" in comp_types
    assert "interface" in comp_types
    assert "model" in comp_types
    assert len(mod.top_level_components) == 4


@pytest.mark.asyncio
async def test_module_with_non_standard_legitimate_entity_types(
    test_session: AsyncSession,
):
    """2. Module containing other legitimate CodeEntity types discovered from the actual parser."""
    project = Project(name="ParserEntitiesProject", local_path="/tmp/test_project", status="READY")
    test_session.add(project)
    await test_session.commit()

    # Types produced by actual parsers: method, module, config, config_key, variable, api, test
    entities = [
        CodeEntity(
            project_id=project.id,
            entity_type="module",
            name="pkg_auth",
            qualified_name="src/auth/pkg.go:pkg:pkg_auth",
            file_path="src/auth/pkg.go",
            start_line=1,
            end_line=1,
            content_hash="h1",
            language="go",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="method",
            name="execute_login",
            qualified_name="src/auth/service.py:AuthService.execute_login",
            file_path="src/auth/service.py",
            start_line=20,
            end_line=35,
            content_hash="h2",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="config",
            name="docker-compose.yml",
            qualified_name="src/auth/docker-compose.yml",
            file_path="src/auth/docker-compose.yml",
            start_line=1,
            end_line=30,
            content_hash="h3",
            language="yaml",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="config_key",
            name="AUTH_PORT",
            qualified_name="src/auth/docker-compose.yml:AUTH_PORT",
            file_path="src/auth/docker-compose.yml",
            start_line=12,
            end_line=12,
            content_hash="h4",
            language="yaml",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="variable",
            name="MAX_ATTEMPTS",
            qualified_name="src/auth/constants.py:MAX_ATTEMPTS",
            file_path="src/auth/constants.py",
            start_line=5,
            end_line=5,
            content_hash="h5",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="api",
            name="login_endpoint",
            qualified_name="src/auth/routes.py:login_endpoint",
            file_path="src/auth/routes.py",
            start_line=40,
            end_line=60,
            content_hash="h6",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="test",
            name="test_login_success",
            qualified_name="src/auth/test_auth.py:test_login_success",
            file_path="src/auth/test_auth.py",
            start_line=10,
            end_line=25,
            content_hash="h7",
            language="python",
        ),
    ]
    test_session.add_all(entities)
    await test_session.commit()

    graph_service = GraphService()
    arch = await graph_service.get_project_architecture(test_session, project.id)

    assert arch is not None
    assert len(arch.modules) == 1
    mod = arch.modules[0]
    assert mod.entity_count == 7

    # NONE of these 7 entity types should be dropped
    comp_types = {c.entity_type for c in mod.top_level_components}
    assert comp_types == {
        "module",
        "method",
        "config",
        "config_key",
        "variable",
        "api",
        "test",
    }
    assert len(mod.top_level_components) == 7

    # API endpoint should also be tracked in primary_apis
    api_names = [a.name for a in arch.primary_apis]
    assert "login_endpoint" in api_names


@pytest.mark.asyncio
async def test_module_with_mixed_types_and_file_container_exclusion(
    test_session: AsyncSession,
):
    """3. Module containing mixed entity types alongside physical file container records."""
    project = Project(name="MixedProject", local_path="/tmp/test_project", status="READY")
    test_session.add(project)
    await test_session.commit()

    entities = [
        # File container entities generated by tree-sitter analyzer
        CodeEntity(
            project_id=project.id,
            entity_type="file",
            name="service.py",
            qualified_name="src/billing/service.py",
            file_path="src/billing/service.py",
            start_line=1,
            end_line=100,
            content_hash="h_file1",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="file",
            name="models.py",
            qualified_name="src/billing/models.py",
            file_path="src/billing/models.py",
            start_line=1,
            end_line=50,
            content_hash="h_file2",
            language="python",
        ),
        # Code entities within the files
        CodeEntity(
            project_id=project.id,
            entity_type="class",
            name="BillingManager",
            qualified_name="src/billing/service.py:BillingManager",
            file_path="src/billing/service.py",
            start_line=10,
            end_line=60,
            content_hash="h_class",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="method",
            name="charge_card",
            qualified_name="src/billing/service.py:BillingManager.charge_card",
            file_path="src/billing/service.py",
            start_line=25,
            end_line=45,
            content_hash="h_method",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="model",
            name="Invoice",
            qualified_name="src/billing/models.py:Invoice",
            file_path="src/billing/models.py",
            start_line=5,
            end_line=30,
            content_hash="h_model",
            language="python",
        ),
    ]
    test_session.add_all(entities)
    await test_session.commit()

    graph_service = GraphService()
    arch = await graph_service.get_project_architecture(test_session, project.id)

    assert arch is not None
    mod = arch.modules[0]

    # Total files recognized: 2
    assert mod.file_count == 2
    # Total entities counted: 5 (including the 2 file containers)
    assert mod.entity_count == 5

    # top_level_components must exclude file containers and include code symbols
    comp_types = [c.entity_type for c in mod.top_level_components]
    assert "file" not in comp_types
    assert "class" in comp_types
    assert "method" in comp_types
    assert "model" in comp_types
    assert len(mod.top_level_components) == 3


@pytest.mark.asyncio
async def test_entity_count_invariant(test_session: AsyncSession):
    """4. Correct entity_count: module.entity_count >= len(module.top_level_components)."""
    project = Project(name="InvariantProject", local_path="/tmp/test_project", status="READY")
    test_session.add(project)
    await test_session.commit()

    entities = [
        CodeEntity(
            project_id=project.id,
            entity_type="file",
            name=f"file_{i}.py",
            qualified_name=f"packages/core/file_{i}.py",
            file_path=f"packages/core/file_{i}.py",
            start_line=1,
            end_line=20,
            content_hash=f"h_file_{i}",
            language="python",
        )
        for i in range(5)
    ] + [
        CodeEntity(
            project_id=project.id,
            entity_type="function",
            name=f"func_{i}",
            qualified_name=f"packages/core/file_0.py:func_{i}",
            file_path="packages/core/file_0.py",
            start_line=i * 5 + 1,
            end_line=i * 5 + 4,
            content_hash=f"h_func_{i}",
            language="python",
        )
        for i in range(10)
    ]
    test_session.add_all(entities)
    await test_session.commit()

    graph_service = GraphService()
    arch = await graph_service.get_project_architecture(test_session, project.id)

    assert arch is not None
    assert arch.total_entities == 15
    for mod in arch.modules:
        assert mod.entity_count >= len(mod.top_level_components)
        assert mod.entity_count == 15
        assert len(mod.top_level_components) == 10


@pytest.mark.asyncio
async def test_top_level_components_deterministic_ordering(test_session: AsyncSession):
    """5. Correct top_level_components ordering (deterministic by file_path, start_line, name)."""
    project = Project(name="OrderingProject", local_path="/tmp/test_project", status="READY")
    test_session.add(project)
    await test_session.commit()

    # Insert in intentionally shuffled order
    entities = [
        CodeEntity(
            project_id=project.id,
            entity_type="function",
            name="zeta",
            qualified_name="src/utils/b.py:zeta",
            file_path="src/utils/b.py",
            start_line=50,
            end_line=60,
            content_hash="h1",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="function",
            name="alpha",
            qualified_name="src/utils/a.py:alpha",
            file_path="src/utils/a.py",
            start_line=10,
            end_line=20,
            content_hash="h2",
            language="python",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="function",
            name="beta",
            qualified_name="src/utils/a.py:beta",
            file_path="src/utils/a.py",
            start_line=30,
            end_line=40,
            content_hash="h3",
            language="python",
        ),
    ]
    test_session.add_all(entities)
    await test_session.commit()

    graph_service = GraphService()
    arch = await graph_service.get_project_architecture(test_session, project.id)

    assert arch is not None
    mod = arch.modules[0]
    names = [c.name for c in mod.top_level_components]
    # a.py:10 (alpha) -> a.py:30 (beta) -> b.py:50 (zeta)
    assert names == ["alpha", "beta", "zeta"]


@pytest.mark.asyncio
async def test_dependency_and_dependent_information_preserved(
    test_session: AsyncSession,
):
    """6. Dependency and dependent information remains correct with relationships."""
    project = Project(name="RelProject", local_path="/tmp/test_project", status="READY")
    test_session.add(project)
    await test_session.commit()

    svc = CodeEntity(
        project_id=project.id,
        entity_type="class",
        name="OrderService",
        qualified_name="apps/api/order.py:OrderService",
        file_path="apps/api/order.py",
        start_line=1,
        end_line=50,
        content_hash="h_svc",
        language="python",
    )
    gateway = CodeEntity(
        project_id=project.id,
        entity_type="class",
        name="PaymentGateway",
        qualified_name="apps/api/payment.py:PaymentGateway",
        file_path="apps/api/payment.py",
        start_line=1,
        end_line=40,
        content_hash="h_gw",
        language="python",
    )
    test_session.add_all([svc, gateway])
    await test_session.commit()

    rel = Relationship(
        project_id=project.id,
        source_entity_id=svc.id,
        target_entity_id=gateway.id,
        relationship_type="calls",
    )
    test_session.add(rel)
    await test_session.commit()

    graph_service = GraphService()
    arch = await graph_service.get_project_architecture(test_session, project.id)

    assert arch is not None
    mod = arch.modules[0]
    comp_map = {c.name: c for c in mod.top_level_components}

    assert "PaymentGateway" in comp_map["OrderService"].dependencies
    assert "OrderService" in comp_map["PaymentGateway"].dependents


@pytest.mark.asyncio
async def test_empty_module_and_file_only_module_behavior(test_session: AsyncSession):
    """7. Empty module / no entities behavior and file-only module behavior."""
    project = Project(name="EmptyProject", local_path="/tmp/test_project", status="READY")
    test_session.add(project)
    await test_session.commit()

    graph_service = GraphService()
    # Case A: Totally empty project
    arch_empty = await graph_service.get_project_architecture(test_session, project.id)
    assert arch_empty is not None
    assert arch_empty.total_entities == 0
    assert arch_empty.total_files == 0
    assert len(arch_empty.modules) == 0

    # Case B: Module with ONLY file container records (e.g. static/empty files)
    file_entity = CodeEntity(
        project_id=project.id,
        entity_type="file",
        name="notes.txt",
        qualified_name="docs/notes.txt",
        file_path="docs/notes.txt",
        start_line=1,
        end_line=10,
        content_hash="h_txt",
        language="unknown",
    )
    test_session.add(file_entity)
    await test_session.commit()

    arch_file_only = await graph_service.get_project_architecture(
        test_session, project.id
    )
    assert arch_file_only is not None
    assert len(arch_file_only.modules) == 1
    mod = arch_file_only.modules[0]
    assert mod.file_count == 1
    assert mod.entity_count == 1
    assert mod.top_level_components == []
    # Invariant must hold
    assert mod.entity_count >= len(mod.top_level_components)


@pytest.mark.asyncio
async def test_future_and_unknown_entity_type_handling(test_session: AsyncSession):
    """8. Future/unknown entity type behavior according to backend contract."""
    project = Project(name="FutureTypeProject", local_path="/tmp/test_project", status="READY")
    test_session.add(project)
    await test_session.commit()

    # Unknown or future language constructs: struct, enum, trait, custom_macro
    future_entities = [
        CodeEntity(
            project_id=project.id,
            entity_type="struct",
            name="Point3D",
            qualified_name="src/math/vector.rs:Point3D",
            file_path="src/math/vector.rs",
            start_line=5,
            end_line=12,
            content_hash="h_struct",
            language="rust",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="enum",
            name="Direction",
            qualified_name="src/math/vector.rs:Direction",
            file_path="src/math/vector.rs",
            start_line=15,
            end_line=25,
            content_hash="h_enum",
            language="rust",
        ),
        CodeEntity(
            project_id=project.id,
            entity_type="custom_macro",
            name="impl_serialize",
            qualified_name="src/math/vector.rs:impl_serialize",
            file_path="src/math/vector.rs",
            start_line=30,
            end_line=45,
            content_hash="h_macro",
            language="rust",
        ),
    ]
    test_session.add_all(future_entities)
    await test_session.commit()

    graph_service = GraphService()
    arch = await graph_service.get_project_architecture(test_session, project.id)

    assert arch is not None
    mod = arch.modules[0]
    assert mod.entity_count == 3

    # All future/unknown entity types must be displayed, not dropped
    comp_types = [c.entity_type for c in mod.top_level_components]
    assert "struct" in comp_types
    assert "enum" in comp_types
    assert "custom_macro" in comp_types
    assert len(mod.top_level_components) == 3
