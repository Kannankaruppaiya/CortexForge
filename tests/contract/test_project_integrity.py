"""Contract tests for the self-dogfooding integrity checker (section 46).

Two things are asserted here. First, that the checker detects each class of drift
it claims to -- proven against synthetic projects built to contain exactly one
contradiction each, so a passing test means the check works rather than that
nothing happened to be wrong. Second, that CortexForge's own repository is free
of the drift the checker can prove, with the one known exception recorded
explicitly rather than silently tolerated.
"""

import os

import pytest

from cortexforge.apps.api.main import app
from cortexforge.integrity import ProjectIntegrityChecker
from cortexforge.integrity.checker import SEVERITY_CONTRADICTION, SEVERITY_MISSING

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_project(
    root,
    pyproject_license: str = "Apache-2.0",
    readme_license: str = "Apache-2.0",
    env_lines: str = "",
    source: str = "",
    include_license_file: bool = True,
):
    """Build a minimal project containing a controlled amount of drift."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "sample"\nversion = "0.1.0"\nlicense = "{pyproject_license}"\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        f"# Sample\n\nSome prose.\n\n## License\n\n{readme_license} License.\n",
        encoding="utf-8",
    )
    if include_license_file:
        (root / "LICENSE").write_text("license text", encoding="utf-8")
    if env_lines:
        (root / ".env.example").write_text(env_lines, encoding="utf-8")
    src = root / "src" / "sample"
    src.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text(source, encoding="utf-8")
    return root


def test_detects_license_contradiction(tmp_path):
    """A README naming a different license from the package metadata is drift."""
    project = _write_project(
        tmp_path / "licensed",
        pyproject_license="Apache-2.0",
        readme_license="MIT",
    )

    report = ProjectIntegrityChecker(str(project)).check()
    license_findings = [f for f in report.findings if f.check == "license"]

    assert license_findings, "a license contradiction must be reported"
    assert license_findings[0].severity == SEVERITY_CONTRADICTION
    assert "MIT" in (license_findings[0].claimed or "")
    assert "Apache-2.0" in (license_findings[0].actual or "")


def test_accepts_a_consistent_license(tmp_path):
    """Matching declarations produce no finding.

    Without this, a checker that reported drift unconditionally would pass the
    detection test above while being useless.
    """
    project = _write_project(
        tmp_path / "consistent", pyproject_license="MIT", readme_license="MIT"
    )
    report = ProjectIntegrityChecker(str(project)).check()
    assert [f for f in report.findings if f.check == "license"] == []


def test_detects_a_missing_license_file(tmp_path):
    """A declared license that is not distributed with the code is reported."""
    project = _write_project(tmp_path / "nolicense", include_license_file=False)
    report = ProjectIntegrityChecker(str(project)).check()

    missing = [
        f
        for f in report.findings
        if f.check == "license" and f.severity == SEVERITY_MISSING
    ]
    assert missing


def test_detects_documented_but_unread_environment_variables(tmp_path):
    """A documented setting nothing reads is drift.

    This is the class of bug that made `CORTEX_EMBEDDINGS_PROVIDER` a no-op: the
    documentation told operators to set a variable the code never consulted.
    """
    project = _write_project(
        tmp_path / "envdrift",
        env_lines="CORTEX_USED=1\nCORTEX_PHANTOM=2\n",
        source='import os\nVALUE = os.environ.get("CORTEX_USED")\n',
    )
    report = ProjectIntegrityChecker(str(project)).check()

    env_findings = [f for f in report.findings if f.check == "environment_variables"]
    assert env_findings
    assert "CORTEX_PHANTOM" in (env_findings[0].claimed or "")
    assert "CORTEX_USED" not in (env_findings[0].claimed or "")


def test_detects_documented_api_paths_that_are_not_served(tmp_path):
    """An endpoint documented but not routed is drift."""
    project = _write_project(tmp_path / "apidrift")
    (project / "README.md").write_text(
        "# Sample\n\nCall `/api/v1/projects` and `/api/v1/imaginary`.\n",
        encoding="utf-8",
    )

    report = ProjectIntegrityChecker(str(project)).check(
        api_paths={"/api/v1/projects"}
    )
    api_findings = [f for f in report.findings if f.check == "api_paths"]

    assert api_findings
    assert "/api/v1/imaginary" in (api_findings[0].claimed or "")
    assert "/api/v1/projects" not in (api_findings[0].claimed or "")


def test_api_path_check_tolerates_path_parameters(tmp_path):
    """Documentation writing a concrete id where the route has a parameter is fine."""
    project = _write_project(tmp_path / "apiparams")
    (project / "README.md").write_text(
        "# Sample\n\nCall `/api/v1/projects/{project_id}/claims`.\n", encoding="utf-8"
    )

    report = ProjectIntegrityChecker(str(project)).check(
        api_paths={"/api/v1/projects/{project_id}/claims"}
    )
    assert [f for f in report.findings if f.check == "api_paths"] == []


@pytest.mark.asyncio
async def test_cortexforge_documentation_matches_itself():
    """CortexForge's own documentation must not contradict its code.

    With the Apache-2.0 LICENSE file present, matching pyproject.toml and README.md,
    the repository is completely free of provable documentation drift.
    """
    report = ProjectIntegrityChecker(REPO_ROOT).check(
        api_paths=set(app.openapi()["paths"])
    )

    assert not report.findings, "\n".join(f.render() for f in report.findings)
