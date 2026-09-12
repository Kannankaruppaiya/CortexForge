"""Integration tests for Git hooks installation, idempotency, and durable job dispatch."""

import os
import subprocess

import pytest
import pytest_asyncio
from click.testing import CliRunner
from sqlalchemy import select

from cortexforge.apps.cli.main import HOOK_START_MARKER, SUPPORTED_HOOKS, cli
from cortexforge.core import db as core_db
from cortexforge.core.models import Base, Job, Project


def _git_run(repo: str, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, timeout=30
    )
    return res.stdout.strip()


@pytest_asyncio.fixture
async def hook_test_env(tmp_path):
    """Set up a real temporary git repo and in-memory test database."""
    repo = str(tmp_path / "git_hook_repo")
    os.makedirs(repo, exist_ok=True)

    _git_run(str(tmp_path), "init", "-q", repo)
    _git_run(repo, "config", "user.email", "hooks@example.com")
    _git_run(repo, "config", "user.name", "HooksTest")

    # Create dummy file & commit
    dummy_file = os.path.join(repo, "README.md")
    with open(dummy_file, "w", encoding="utf-8") as f:
        f.write("# Hooks Test Repo\n")
    _git_run(repo, "add", "-A")
    _git_run(repo, "commit", "-q", "-m", "initial commit")

    # Create test database
    db_file = tmp_path / "test_cortexforge.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    engine, factory = core_db.create_cortex_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Register project in test DB
    canonical_repo = os.path.realpath(repo)
    async with factory() as session:
        proj = Project(
            name="GitHooksTestProject",
            local_path=canonical_repo,
            status="READY",
            default_branch="main",
        )
        session.add(proj)
        await session.commit()

    # Override global session maker and engine so CLI calls use this test database
    old_engine = core_db.engine
    core_db.set_engine(engine)

    yield repo, factory, engine

    core_db.set_engine(old_engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_git_hooks_lifecycle_and_job_dispatch(hook_test_env):
    """Test hook installation, idempotency, job dispatch, and uninstallation."""
    repo, factory, _ = hook_test_env
    runner = CliRunner()

    # 1. Install hooks
    result = runner.invoke(cli, ["hooks", "install", repo])
    assert result.exit_code == 0
    assert "installed" in result.output.lower()

    hooks_dir = os.path.join(repo, ".git", "hooks")
    for hook_name in SUPPORTED_HOOKS:
        hook_path = os.path.join(hooks_dir, hook_name)
        assert os.path.exists(hook_path), f"Hook {hook_name} was not created"
        with open(hook_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert HOOK_START_MARKER in content
        assert hook_name in content

    # 2. Idempotent install: running again should not duplicate
    result_repeat = runner.invoke(cli, ["hooks", "install", repo])
    assert result_repeat.exit_code == 0
    assert "already installed" in result_repeat.output.lower()

    # Count occurrences of hook start marker in post-commit
    post_commit_path = os.path.join(hooks_dir, "post-commit")
    with open(post_commit_path, "r", encoding="utf-8") as f:
        repeat_content = f.read()
    assert repeat_content.count(HOOK_START_MARKER) == 1

    # 3. Simulate hook firing via `cortex hooks handle`
    handle_res = runner.invoke(
        cli, ["hooks", "handle", "--hook", "post-commit", "--path", repo]
    )
    assert handle_res.exit_code == 0

    # Verify a durable job was enqueued in the database
    async with factory() as session:
        jobs_stmt = select(Job).where(Job.job_type == "scan_project")
        jobs = (await session.execute(jobs_stmt)).scalars().all()
        assert len(jobs) >= 1
        job = jobs[0]
        assert job.status == "PENDING"
        assert job.parameters.get("hook") == "post-commit"
        assert job.parameters.get("incremental") is True

    # 4. Uninstall hooks
    uninst_res = runner.invoke(cli, ["hooks", "uninstall", repo])
    assert uninst_res.exit_code == 0
    assert "removed" in uninst_res.output.lower()

    for hook_name in SUPPORTED_HOOKS:
        hook_path = os.path.join(hooks_dir, hook_name)
        assert not os.path.exists(hook_path), (
            f"Hook {hook_name} should have been removed"
        )

    # 5. Idempotent uninstall: running again should succeed without error
    uninst_repeat = runner.invoke(cli, ["hooks", "uninstall", repo])
    assert uninst_repeat.exit_code == 0
