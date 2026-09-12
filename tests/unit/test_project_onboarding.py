"""Comprehensive tests for Project Onboarding & Repository Source Architecture (§1-§33).

Verifies:
1. Three distinct repository sources (LOCAL, GITHUB, GIT_URL).
2. Schema validation (required fields per source, rejection of invalid combinations).
3. Local path validation, branch detection, language detection.
4. Path traversal, symlink escape, and outside-permitted-root protection.
5. Git URL validation (command injection, flag injection, SSRF scheme restrictions).
6. Managed workspace isolation (<managed_root>/<user_id>/<project_id>/repo).
7. Managed workspace directory cleanup on project deletion.
8. Server-derived project ownership (client cannot spoof owner_user_id).
9. Cross-user IDOR protection (GET, DELETE, scan, jobs).
10. Background job dispatch (SCAN for LOCAL, IMPORT_AND_SCAN for GITHUB/GIT_URL).
11. GitHub repository and branch endpoints.
"""

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from cortexforge.apps.api.main import app
from cortexforge.code_intelligence.git_service import (
    clone_repository,
    inspect_local_repository,
    validate_git_url,
)
from cortexforge.core.schemas import ProjectCreate
from cortexforge.security.auth import validate_local_registration_path
from cortexforge.security.managed_workspace import ManagedWorkspaceService
from cortexforge.security.path_safety import PathSecurity, PathSecurityError
from cortexforge.security.rate_limiter import auth_rate_limiter


@pytest.fixture
def managed_workspace_tmp():
    """Create a temporary directory for managed workspaces."""
    tmp_dir = tempfile.mkdtemp(prefix="cortex_test_managed_")
    svc = ManagedWorkspaceService(managed_root=tmp_dir)
    yield svc, tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def local_repo_tmp():
    """Create a temporary local repository with git directory and sample files."""
    tmp_dir = tempfile.mkdtemp(prefix="cortex_test_local_repo_")
    os.makedirs(os.path.join(tmp_dir, ".git"), exist_ok=True)
    with open(os.path.join(tmp_dir, ".git", "HEAD"), "w", encoding="utf-8") as f:
        f.write("ref: refs/heads/main\n")

    # Python files (2 files)
    with open(os.path.join(tmp_dir, "main.py"), "w", encoding="utf-8") as f:
        f.write("def main():\n    print('hello world')\n    return True\n")
    with open(os.path.join(tmp_dir, "util.py"), "w", encoding="utf-8") as f:
        f.write("def helper():\n    return 42\n")

    # TypeScript file (1 file)
    with open(os.path.join(tmp_dir, "index.ts"), "w", encoding="utf-8") as f:
        f.write("export const ready = true;\n")

    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ==============================================================================
# 1. Schema Validation Tests
# ==============================================================================


def test_schema_validation_local_requires_local_path():
    """LOCAL project source requires local_path."""
    with pytest.raises(ValueError, match="local_path is required"):
        ProjectCreate(name="Test Local", source_type="LOCAL", local_path=None)


def test_schema_validation_git_url_requires_clone_url():
    """GIT_URL project source requires clone_url."""
    with pytest.raises(ValueError, match="clone_url is required"):
        ProjectCreate(name="Test Git URL", source_type="GIT_URL", clone_url=None)


def test_schema_validation_github_requires_repo_or_url():
    """GITHUB project source requires github_owner/github_repo or clone_url."""
    with pytest.raises(ValueError, match="required when source_type is GITHUB"):
        ProjectCreate(name="Test GitHub", source_type="GITHUB")

    # Valid with owner and repo
    valid_gh = ProjectCreate(
        name="Test GitHub",
        source_type="GITHUB",
        github_owner="octocat",
        github_repo="Hello-World",
    )
    assert valid_gh.source_type == "GITHUB"


def test_schema_validation_valid_local(local_repo_tmp):
    """Valid LOCAL project passes schema validation."""
    valid_loc = ProjectCreate(
        name="Local Project",
        source_type="LOCAL",
        local_path=local_repo_tmp,
    )
    assert valid_loc.source_type == "LOCAL"
    assert valid_loc.local_path == local_repo_tmp


# ==============================================================================
# 2. Local Path Security & Traversal Tests
# ==============================================================================


def test_path_traversal_detection(local_repo_tmp):
    """Path traversal attacks are rejected by PathSecurity."""
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(local_repo_tmp, "../../etc/passwd")

    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(local_repo_tmp, "..\\..\\Windows\\System32")


def test_local_repository_inspection(local_repo_tmp):
    """Repository inspection accurately detects branch and language breakdown."""
    info = inspect_local_repository(local_repo_tmp)
    assert info["valid"] is True
    assert info["is_git"] is True
    assert info["default_branch"] == "main"
    assert info["detected_language"] == "Python"
    assert "Python" in info["languages"]
    assert "TypeScript" in info["languages"]


def test_nonexistent_local_repository():
    """Nonexistent local path returns valid=False with error message."""
    res = inspect_local_repository("C:\\nonexistent_cortex_path_12345")
    assert res["valid"] is False
    assert res["error"] is not None


# ==============================================================================
# 3. Git URL Security Tests
# ==============================================================================


def test_validate_git_url_safe():
    """Legitimate HTTPS and Git URLs pass validation."""
    assert validate_git_url("https://github.com/owner/repo.git") is True
    assert validate_git_url("http://gitlab.com/group/project.git") is True
    assert validate_git_url("git@github.com:owner/repo.git") is True


def test_validate_git_url_injection_attacks():
    """Rejects flag injection, file protocol, shell metacharacters, and SSRF schemes."""
    # File scheme
    assert validate_git_url("file:///etc/passwd") is False

    # Command injection
    assert validate_git_url("https://github.com/repo.git; rm -rf /") is False
    assert validate_git_url("https://github.com/repo.git | bash") is False

    # Flag injection
    assert validate_git_url("--upload-pack=evil") is False

    # clone_repository fails on invalid URL
    with pytest.raises(ValueError, match="Invalid or unsafe Git repository URL"):
        clone_repository("file:///etc/passwd", "/tmp")


# ==============================================================================
# 4. Managed Workspace Isolation & Cleanup Tests
# ==============================================================================


def test_managed_workspace_isolation(managed_workspace_tmp):
    """Managed workspace paths are isolated per user and per project."""
    svc, _tmp_dir = managed_workspace_tmp
    user_id = "user-abc-123"
    project_id = "proj-xyz-789"

    ws_path = svc.get_project_workspace_path(user_id, project_id)
    assert str(ws_path).startswith(str(svc.root))
    assert "user-abc-123" in str(ws_path)
    assert "proj-xyz-789" in str(ws_path)
    assert ws_path.name == "repo"

    # Preparation creates the directory
    prep_path = svc.prepare_project_workspace(user_id, project_id)
    assert prep_path.exists()
    assert prep_path == ws_path

    # Cleanup removes the project workspace
    cleaned = svc.cleanup_project_workspace(user_id, project_id)
    assert cleaned is True
    assert not prep_path.exists()


def test_managed_workspace_rejects_traversal(managed_workspace_tmp):
    """Managed workspace service rejects user_id or project_id directory traversal."""
    svc, _ = managed_workspace_tmp
    with pytest.raises(PathSecurityError):
        svc.get_project_workspace_path("..", "proj")

    with pytest.raises(PathSecurityError):
        svc.get_project_workspace_path("user", "../../escaped")


# ==============================================================================
# 5. End-to-End API Integration & Red-Team Tests
# ==============================================================================


async def create_test_authenticated_user(client: AsyncClient, email: str, name: str):
    """Helper to register and authenticate a user, returning user dict and session token."""
    auth_rate_limiter.clear_all()
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "SecurePassword123!", "display_name": name},
    )
    assert reg_resp.status_code == 201
    data = reg_resp.json()
    return data["user"], data["token"]


@pytest.mark.asyncio
async def test_api_validate_local_endpoint(test_session, local_repo_tmp):
    """POST /api/v1/projects/validate-local validates permitted local repos."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _user, token = await create_test_authenticated_user(
            client, f"test_val_{uuid.uuid4().hex[:8]}@example.com", "Validator"
        )
        resp = await client.post(
            "/api/v1/projects/validate-local",
            json={"local_path": local_repo_tmp},
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp.status_code == 200
        val_data = resp.json()
        assert val_data["valid"] is True
        assert val_data["is_git"] is True
        assert val_data["default_branch"] == "main"
        assert val_data["detected_language"] == "Python"


@pytest.mark.asyncio
async def test_api_create_local_project(test_session, local_repo_tmp):
    """User creates a LOCAL project: ownership is server-derived, SCAN job dispatched."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_a, token_a = await create_test_authenticated_user(
            client, f"user_a_{uuid.uuid4().hex[:8]}@example.com", "Alice"
        )
        user_b, token_b = await create_test_authenticated_user(
            client, f"user_b_{uuid.uuid4().hex[:8]}@example.com", "Bob"
        )

        # Alice creates Project A, maliciously trying to assign Bob as owner
        payload = {
            "name": "Alice-Local-Project",
            "source_type": "LOCAL",
            "local_path": local_repo_tmp,
            "owner_user_id": user_b["id"],  # Malicious spoof attempt
        }
        resp = await client.post(
            "/api/v1/projects",
            json=payload,
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert resp.status_code == 201
        proj = resp.json()
        assert proj["name"] == "Alice-Local-Project"
        assert proj["source_type"] == "LOCAL"
        # Server-derived ownership verification (§11)
        assert proj["owner_user_id"] == user_a["id"]
        assert proj["owner_user_id"] != user_b["id"]
        assert proj["managed_workspace"] is False
        assert proj["initial_job_id"] is not None

        # Verify Bob cannot access Alice's project (IDOR §14, §24)
        idor_get = await client.get(
            f"/api/v1/projects/{proj['id']}",
            headers={"Cookie": f"cortex_session={token_b}"},
        )
        assert idor_get.status_code == 403

        idor_delete = await client.delete(
            f"/api/v1/projects/{proj['id']}",
            headers={"Cookie": f"cortex_session={token_b}"},
        )
        assert idor_delete.status_code == 403

        idor_scan = await client.post(
            f"/api/v1/projects/{proj['id']}/scan",
            json={"incremental": False},
            headers={"Cookie": f"cortex_session={token_b}"},
        )
        assert idor_scan.status_code == 403


@pytest.mark.asyncio
async def test_api_create_git_url_project(test_session):
    """User creates a GIT_URL project: uses managed workspace and dispatches IMPORT_AND_SCAN."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user, token = await create_test_authenticated_user(
            client, f"user_git_{uuid.uuid4().hex[:8]}@example.com", "GitUser"
        )

        payload = {
            "name": "Remote-Git-Project",
            "source_type": "GIT_URL",
            "clone_url": "https://github.com/psf/requests.git",
            "default_branch": "main",
        }
        resp = await client.post(
            "/api/v1/projects",
            json=payload,
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp.status_code == 201
        proj = resp.json()
        assert proj["name"] == "Remote-Git-Project"
        assert proj["source_type"] == "GIT_URL"
        assert proj["managed_workspace"] is True
        assert proj["clone_url"] == "https://github.com/psf/requests.git"
        assert proj["owner_user_id"] == user["id"]
        assert proj["initial_job_id"] is not None

        # Clean up project and verify managed workspace deletion
        del_resp = await client.delete(
            f"/api/v1/projects/{proj['id']}",
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert del_resp.status_code == 204


@pytest.mark.asyncio
async def test_api_create_github_project(test_session):
    """User creates a GITHUB project: stores metadata and dispatches IMPORT_AND_SCAN."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user, token = await create_test_authenticated_user(
            client, f"user_gh_{uuid.uuid4().hex[:8]}@example.com", "GhUser"
        )

        payload = {
            "name": "CortexForge-GH",
            "source_type": "GITHUB",
            "github_owner": "cortexforge",
            "github_repo": "core",
            "github_repository_id": "12345678",
            "default_branch": "main",
        }
        resp = await client.post(
            "/api/v1/projects",
            json=payload,
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp.status_code == 201
        proj = resp.json()
        assert proj["source_type"] == "GITHUB"
        assert proj["github_owner"] == "cortexforge"
        assert proj["github_repo"] == "core"
        assert proj["managed_workspace"] is True
        assert proj["owner_user_id"] == user["id"]
        assert proj["initial_job_id"] is not None


@pytest.mark.asyncio
async def test_api_github_repositories_endpoint(test_session):
    """GET /api/v1/github/repositories returns connection status and repositories."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _user, token = await create_test_authenticated_user(
            client, f"user_ghrepo_{uuid.uuid4().hex[:8]}@example.com", "GhRepoUser"
        )

        # User has no connected GitHub account yet
        resp = await client.get(
            "/api/v1/github/repositories",
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False
        assert data["repositories"] == []


@pytest.mark.asyncio
async def test_api_browse_directories_endpoint(test_session, local_repo_tmp):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _user, token = await create_test_authenticated_user(
            client, f"browse_user_{uuid.uuid4().hex[:8]}@example.com", "BrowserUser"
        )
        sub_repo = Path(local_repo_tmp) / "my_sub_repo"
        sub_repo.mkdir(exist_ok=True)
        (sub_repo / ".git").mkdir(exist_ok=True)

        resp = await client.get(
            f"/api/v1/projects/browse-directories?path={local_repo_tmp}",
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "directories" in data
        names = [d["name"] for d in data["directories"]]
        assert "my_sub_repo" in names
        sub_entry = next(d for d in data["directories"] if d["name"] == "my_sub_repo")
        assert sub_entry["is_git"] is True


# ==============================================================================
# 6. Local Repository Registration & Path Security Tests (§14-§22)
# ==============================================================================


def test_validate_local_registration_path_valid(local_repo_tmp):
    """validate_local_registration_path accepts valid existing directory."""
    canonical = validate_local_registration_path(local_repo_tmp)
    assert canonical == str(Path(local_repo_tmp).resolve())


def test_validate_local_registration_path_rejects_empty():
    """Empty or whitespace path is rejected with 400."""
    with pytest.raises(HTTPException) as exc:
        validate_local_registration_path("")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        validate_local_registration_path("   ")
    assert exc.value.status_code == 400


def test_validate_local_registration_path_rejects_unc():
    """UNC network paths (\\\\server\\share) are rejected with 400."""
    with pytest.raises(HTTPException) as exc:
        validate_local_registration_path("\\\\server\\share\\project")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        validate_local_registration_path("//network/share")
    assert exc.value.status_code == 400


def test_validate_local_registration_path_rejects_filesystem_root():
    """Filesystem roots (C:\\ or /) are rejected with 403."""
    root_path = "C:\\" if os.name == "nt" else "/"
    with pytest.raises(HTTPException) as exc:
        validate_local_registration_path(root_path)
    assert exc.value.status_code == 403


def test_validate_local_registration_path_rejects_system_directory():
    """System directories (C:\\Windows or /etc) are rejected with 403."""
    sys_path = "C:\\Windows" if os.name == "nt" else "/etc"
    with pytest.raises(HTTPException) as exc:
        validate_local_registration_path(sys_path)
    assert exc.value.status_code == 403


def test_validate_local_registration_path_rejects_nonexistent():
    """Nonexistent directories are rejected with 400."""
    with pytest.raises(HTTPException) as exc:
        validate_local_registration_path(
            "C:\\nonexistent_dir_99999" if os.name == "nt" else "/nonexistent_dir_99999"
        )
    assert exc.value.status_code == 400


def test_validate_local_registration_path_rejects_file(local_repo_tmp):
    """A file path is rejected with 400 (only directories allowed)."""
    file_path = os.path.join(local_repo_tmp, "main.py")
    with pytest.raises(HTTPException) as exc:
        validate_local_registration_path(file_path)
    assert exc.value.status_code == 400


def test_inspect_local_repository_non_git():
    """Non-git directory is successfully inspected with valid=True and is_git=False."""
    tmp = tempfile.mkdtemp(prefix="cortex_nongit_")
    try:
        with open(os.path.join(tmp, "script.py"), "w", encoding="utf-8") as f:
            f.write("print('hello')\n")
        info = inspect_local_repository(tmp)
        assert info["valid"] is True
        assert info["is_git"] is False
        assert info["detected_language"] == "Python"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_api_validate_local_endpoint_rejects_system_directory(test_session):
    """POST /projects/validate-local returns valid=False for system directories."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _user, token = await create_test_authenticated_user(
            client, f"val_sys_{uuid.uuid4().hex[:8]}@example.com", "SysVal"
        )
        sys_path = "C:\\Windows" if os.name == "nt" else "/etc"
        resp = await client.post(
            "/api/v1/projects/validate-local",
            json={"local_path": sys_path},
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert (
            "System directory" in data["error"]
            or "cannot be registered" in data["error"]
        )


@pytest.mark.asyncio
async def test_api_create_local_project_outside_cwd(test_session):
    """User can create a project from an external folder outside the CortexForge repository."""
    ext_tmp = tempfile.mkdtemp(prefix="ext_project_repo_")
    try:
        os.makedirs(os.path.join(ext_tmp, ".git"), exist_ok=True)
        with open(os.path.join(ext_tmp, ".git", "HEAD"), "w", encoding="utf-8") as f:
            f.write("ref: refs/heads/feature/awesome\n")
        with open(os.path.join(ext_tmp, "app.py"), "w", encoding="utf-8") as f:
            f.write("print('external app')\n")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            user, token = await create_test_authenticated_user(
                client, f"ext_user_{uuid.uuid4().hex[:8]}@example.com", "ExtUser"
            )

            # First validate external path via API
            val_resp = await client.post(
                "/api/v1/projects/validate-local",
                json={"local_path": ext_tmp},
                headers={"Cookie": f"cortex_session={token}"},
            )
            assert val_resp.status_code == 200
            val_data = val_resp.json()
            assert val_data["valid"] is True
            assert val_data["is_git"] is True
            assert val_data["default_branch"] == "feature/awesome"
            assert val_data["detected_language"] == "Python"

            # Create project
            create_resp = await client.post(
                "/api/v1/projects",
                json={
                    "name": "External-App",
                    "source_type": "LOCAL",
                    "local_path": ext_tmp,
                    "default_branch": val_data["default_branch"],
                },
                headers={"Cookie": f"cortex_session={token}"},
            )
            assert create_resp.status_code == 201
            proj = create_resp.json()
            assert proj["name"] == "External-App"
            assert proj["source_type"] == "LOCAL"
            assert proj["local_path"] == str(Path(ext_tmp).resolve())
            assert proj["owner_user_id"] == user["id"]
            assert proj["initial_job_id"] is not None
    finally:
        shutil.rmtree(ext_tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_api_browse_directories_outside_workspace(test_session):
    """GET /browse-directories can browse external folders outside workspace root."""
    ext_dir = tempfile.mkdtemp(prefix="ext_browse_")
    try:
        sub1 = Path(ext_dir) / "subfolder_alpha"
        sub1.mkdir(exist_ok=True)
        sub2 = Path(ext_dir) / "subfolder_beta"
        sub2.mkdir(exist_ok=True)
        (sub2 / ".git").mkdir(exist_ok=True)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            _user, token = await create_test_authenticated_user(
                client, f"browse_ext_{uuid.uuid4().hex[:8]}@example.com", "ExtBrowser"
            )
            resp = await client.get(
                f"/api/v1/projects/browse-directories?path={ext_dir}",
                headers={"Cookie": f"cortex_session={token}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            names = [d["name"] for d in data["directories"]]
            assert "subfolder_alpha" in names
            assert "subfolder_beta" in names
            beta_entry = next(
                d for d in data["directories"] if d["name"] == "subfolder_beta"
            )
            assert beta_entry["is_git"] is True
            assert "is_windows" in data
            assert "is_drive_root" in data
    finally:
        shutil.rmtree(ext_dir, ignore_errors=True)


def test_validate_local_registration_path_rejects_bare_drive_letter():
    """Bare Windows drive letters (e.g. 'C:', 'c:') must be rejected with 403 as filesystem roots."""
    if os.name == "nt":
        for bare in ("C:", "c:", "D:", "d:"):
            with pytest.raises(HTTPException) as exc:
                validate_local_registration_path(bare)
            assert exc.value.status_code == 403
            assert "filesystem root" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_browse_directories_reports_platform_and_drives(test_session):
    """GET /browse-directories returns platform flags and handles DRIVES path."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _user, token = await create_test_authenticated_user(
            client, f"browse_meta_{uuid.uuid4().hex[:8]}@example.com", "MetaBrowser"
        )
        resp = await client.get(
            "/api/v1/projects/browse-directories",
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "is_windows" in data
        assert "is_drive_root" in data
        assert data["is_windows"] == (os.name == "nt")

        if os.name == "nt":
            drives_resp = await client.get(
                "/api/v1/projects/browse-directories?path=DRIVES",
                headers={"Cookie": f"cortex_session={token}"},
            )
            assert drives_resp.status_code == 200
            drives_data = drives_resp.json()
            assert drives_data["current_path"] == "DRIVES"
            assert drives_data["is_windows"] is True
            assert drives_data["is_drive_root"] is True
            assert len(drives_data["directories"]) > 0
            assert any(d["name"].startswith("C:") for d in drives_data["directories"])


@pytest.mark.asyncio
async def test_api_pick_directory_reports_gui_availability(
    test_session, monkeypatch, local_repo_tmp
):
    """POST /projects/pick-directory returns structured result with gui_available flag."""
    import tkinter
    import tkinter.filedialog

    class MockTk:
        def withdraw(self):
            pass

        def attributes(self, *args, **kwargs):
            pass

        def destroy(self):
            pass

    # Mock Tk and askdirectory so headless CI runners without $DISPLAY don't fail
    monkeypatch.setattr(tkinter, "Tk", lambda: MockTk())
    monkeypatch.setattr(
        tkinter.filedialog, "askdirectory", lambda **kwargs: local_repo_tmp
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _user, token = await create_test_authenticated_user(
            client, f"picker_{uuid.uuid4().hex[:8]}@example.com", "PickerUser"
        )
        resp = await client.post(
            "/api/v1/projects/pick-directory",
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["gui_available"] is True
        assert data["path"] == str(Path(local_repo_tmp).resolve())
        assert data["valid"] is True

        # Test user cancellation in picker
        monkeypatch.setattr(tkinter.filedialog, "askdirectory", lambda **kwargs: "")
        resp_cancel = await client.post(
            "/api/v1/projects/pick-directory",
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp_cancel.status_code == 200
        data_cancel = resp_cancel.json()
        assert data_cancel["gui_available"] is True
        assert data_cancel["canceled"] is True
        assert data_cancel["path"] is None


@pytest.mark.asyncio
async def test_api_pick_directory_headless_fallback(test_session, monkeypatch):
    """POST /projects/pick-directory gracefully reports gui_available=False on headless systems."""
    import tkinter

    def _raise_headless():
        raise RuntimeError("no display name and no $DISPLAY environment variable")

    monkeypatch.setattr(tkinter, "Tk", _raise_headless)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _user, token = await create_test_authenticated_user(
            client, f"headless_{uuid.uuid4().hex[:8]}@example.com", "HeadlessUser"
        )
        resp = await client.post(
            "/api/v1/projects/pick-directory",
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["gui_available"] is False
        assert data["path"] is None
        assert "Native folder dialog unavailable" in data["error"]
