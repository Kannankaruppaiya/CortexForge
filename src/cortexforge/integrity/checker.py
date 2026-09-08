"""Detect drift between what a project claims and what it contains (section 46).

CortexForge treats repository text as untrusted input. Documentation is
repository text. So the same scepticism applies to CortexForge's own README, and
this checker is the mechanism: it compares declarations against the artifacts that
would have to exist for them to be true.

The checks are deliberately narrow and evidence-based. Each one names a specific
contradiction it can prove -- a license declared two ways, a documented endpoint
the router does not serve, an environment variable nothing reads -- rather than
attempting to judge whether prose is broadly accurate. A checker that guessed
would produce noise, and noise in an integrity report is worse than no report.

Findings are observations, not verdicts. They say what disagrees with what, and
leave the resolution to a person, because which side of a contradiction is wrong
is a judgement the repository cannot make for itself.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Severity vocabulary. CONTRADICTION means two artifacts state incompatible
# things; MISSING means something referenced does not exist; UNVERIFIABLE means
# a claim was found that this checker has no way to confirm either way.
SEVERITY_CONTRADICTION = "CONTRADICTION"
SEVERITY_MISSING = "MISSING"
SEVERITY_UNVERIFIABLE = "UNVERIFIABLE"


@dataclass
class IntegrityFinding:
    """One provable disagreement between a claim and the repository."""

    check: str
    severity: str
    summary: str
    claimed: str | None = None
    actual: str | None = None
    sources: list[str] = field(default_factory=list)

    def render(self) -> str:
        parts = [f"[{self.severity}] {self.check}: {self.summary}"]
        if self.claimed is not None:
            parts.append(f"  claimed: {self.claimed}")
        if self.actual is not None:
            parts.append(f"  actual:  {self.actual}")
        if self.sources:
            parts.append(f"  sources: {', '.join(self.sources)}")
        return "\n".join(parts)


@dataclass
class IntegrityReport:
    """The outcome of checking a project against its own documentation."""

    root: str
    findings: list[IntegrityFinding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    checks_skipped: dict[str, str] = field(default_factory=dict)

    @property
    def is_consistent(self) -> bool:
        return not self.findings

    def by_severity(self, severity: str) -> list[IntegrityFinding]:
        return [f for f in self.findings if f.severity == severity]

    def render(self) -> str:
        if not self.findings:
            return (
                f"No documentation drift detected across {len(self.checks_run)} check(s)."
            )
        lines = [f"{len(self.findings)} integrity finding(s):", ""]
        lines.extend(finding.render() for finding in self.findings)
        if self.checks_skipped:
            lines.append("")
            lines.append("Skipped checks:")
            lines.extend(
                f"  {name}: {reason}" for name, reason in self.checks_skipped.items()
            )
        return "\n".join(lines)


_LICENSE_IN_PROSE = re.compile(
    r"\b(MIT|Apache[- ]?2\.0|BSD[- ]?3[- ]?Clause|BSD[- ]?2[- ]?Clause|GPL[- ]?3\.0|"
    r"AGPL[- ]?3\.0|MPL[- ]?2\.0|Unlicense|Proprietary)\b",
    re.IGNORECASE,
)
_ENV_VAR_IN_DOCS = re.compile(r"\b(CORTEX_[A-Z0-9_]+)\b")
_API_PATH_IN_DOCS = re.compile(r"`(/api/v1/[A-Za-z0-9_{}/\-]+)`")


def _normalize_license(value: str) -> str:
    """Reduce a license name to a comparable form."""
    collapsed = re.sub(r"[\s\-]", "", value).upper()
    return collapsed.removesuffix("LICENSE")


class ProjectIntegrityChecker:
    """Compares a project's declarations against the artifacts that support them."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def check(self, api_paths: set[str] | None = None) -> IntegrityReport:
        """Run every check, collecting provable contradictions.

        ``api_paths`` is supplied by the caller (from the live OpenAPI schema)
        rather than imported here, so this module stays free of a dependency on
        the web application it inspects.
        """
        report = IntegrityReport(root=str(self.root))

        self._check_license(report)
        self._check_environment_variables(report)
        self._check_documented_api_paths(report, api_paths)
        self._check_test_count_claims(report)

        return report

    # ----------------------------------------------------------- individual

    def _check_license(self, report: IntegrityReport) -> None:
        """The package metadata and the README must name the same license.

        This is the specification's own example of documentation drift, and it is
        worth catching because the two are read by different audiences: package
        metadata by tooling and redistributors, the README by people.
        """
        report.checks_run.append("license")

        pyproject = self.root / "pyproject.toml"
        readme = self.root / "README.md"
        if not pyproject.exists():
            report.checks_skipped["license"] = "no pyproject.toml"
            return

        with open(pyproject, "rb") as handle:
            metadata: dict[str, Any] = tomllib.load(handle)
        declared = metadata.get("project", {}).get("license")
        if isinstance(declared, dict):
            declared = declared.get("text") or declared.get("file")
        if not declared:
            report.checks_skipped["license"] = "pyproject declares no license"
            return

        license_file = next(
            (p for p in self.root.glob("LICENSE*") if p.is_file()), None
        )
        if license_file is None:
            report.findings.append(
                IntegrityFinding(
                    check="license",
                    severity=SEVERITY_MISSING,
                    summary=(
                        "The package declares a license but the repository contains "
                        "no LICENSE file, so the declared terms are not distributed "
                        "with the code."
                    ),
                    claimed=str(declared),
                    actual="no LICENSE file in the repository root",
                    sources=["pyproject.toml"],
                )
            )

        if not readme.exists():
            return

        prose = readme.read_text(encoding="utf-8", errors="ignore")
        # Only the License section is scanned: a license name mentioned while
        # discussing a dependency is not a claim about this project.
        section = re.split(r"^#+\s*.*license.*$", prose, flags=re.IGNORECASE | re.MULTILINE)
        candidate = section[-1][:400] if len(section) > 1 else ""
        mentioned = _LICENSE_IN_PROSE.findall(candidate)
        if not mentioned:
            return

        declared_norm = _normalize_license(str(declared))
        if not any(_normalize_license(name) == declared_norm for name in mentioned):
            report.findings.append(
                IntegrityFinding(
                    check="license",
                    severity=SEVERITY_CONTRADICTION,
                    summary=(
                        "The README's license section names a different license from "
                        "the one the package metadata declares."
                    ),
                    claimed=f"README: {', '.join(sorted(set(mentioned)))}",
                    actual=f"pyproject.toml: {declared}",
                    sources=["README.md", "pyproject.toml"],
                )
            )

    def _check_environment_variables(self, report: IntegrityReport) -> None:
        """Every documented CORTEX_* variable must be read by some code path.

        A documented setting that nothing reads is worse than an undocumented
        one: a person configures it, observes no effect, and has no way to tell
        whether the feature is broken or the variable is fiction. This project
        shipped exactly that bug (`CORTEX_EMBEDDINGS_PROVIDER`).
        """
        report.checks_run.append("environment_variables")

        env_example = self.root / ".env.example"
        source_root = self.root / "src"
        if not env_example.exists() or not source_root.exists():
            report.checks_skipped["environment_variables"] = (
                "no .env.example or no src directory"
            )
            return

        documented: set[str] = set()
        for line in env_example.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip().lstrip("#").strip()
            if "=" not in stripped:
                continue
            name = stripped.split("=", 1)[0].strip()
            if _ENV_VAR_IN_DOCS.fullmatch(name):
                documented.add(name)

        if not documented:
            return

        source_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in source_root.rglob("*.py")
        )

        unread = sorted(name for name in documented if name not in source_text)
        if unread:
            report.findings.append(
                IntegrityFinding(
                    check="environment_variables",
                    severity=SEVERITY_CONTRADICTION,
                    summary=(
                        "These environment variables are documented but no source "
                        "file reads them, so setting them has no effect."
                    ),
                    claimed=", ".join(unread),
                    actual="not referenced anywhere under src/",
                    sources=[".env.example"],
                )
            )

    def _check_documented_api_paths(
        self, report: IntegrityReport, api_paths: set[str] | None
    ) -> None:
        """Every API path quoted in the docs must exist in the served schema."""
        report.checks_run.append("api_paths")

        if api_paths is None:
            report.checks_skipped["api_paths"] = (
                "caller supplied no OpenAPI paths to compare against"
            )
            return

        documented: set[str] = set()
        for doc in [self.root / "README.md", *(self.root / "docs").rglob("*.md")]:
            if doc.exists():
                documented.update(
                    _API_PATH_IN_DOCS.findall(doc.read_text(encoding="utf-8", errors="ignore"))
                )

        if not documented:
            return

        def matches(path: str) -> bool:
            # Documentation writes concrete ids where the route has parameters,
            # so compare shapes rather than literal strings.
            pattern = re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(path).replace(r"\{", "{").replace(r"\}", "}"))
            pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pattern)
            return any(re.fullmatch(pattern, served) for served in api_paths) or path in api_paths

        missing = sorted(path for path in documented if not matches(path))
        if missing:
            report.findings.append(
                IntegrityFinding(
                    check="api_paths",
                    severity=SEVERITY_MISSING,
                    summary="These API paths are documented but the application does not serve them.",
                    claimed=", ".join(missing),
                    actual=f"{len(api_paths)} paths served",
                    sources=["README.md", "docs/"],
                )
            )

    def _check_test_count_claims(self, report: IntegrityReport) -> None:
        """Documentation must not state a test count it cannot keep current.

        A hardcoded "run all 51 tests" is wrong the moment a test is added, and
        being confidently wrong about a verifiable number undermines every other
        claim in the same document.
        """
        report.checks_run.append("test_count_claims")

        readme = self.root / "README.md"
        if not readme.exists():
            report.checks_skipped["test_count_claims"] = "no README.md"
            return

        prose = readme.read_text(encoding="utf-8", errors="ignore")
        claims = re.findall(r"\ball\s+(\d+)\s+(?:unit and integration\s+)?tests\b", prose, re.IGNORECASE)
        if not claims:
            return

        tests_root = self.root / "tests"
        actual = (
            sum(
                len(re.findall(r"^\s*(?:async\s+)?def\s+test_", path.read_text(encoding="utf-8", errors="ignore"), re.MULTILINE))
                for path in tests_root.rglob("test_*.py")
            )
            if tests_root.exists()
            else 0
        )

        for claimed in claims:
            if int(claimed) != actual:
                report.findings.append(
                    IntegrityFinding(
                        check="test_count_claims",
                        severity=SEVERITY_CONTRADICTION,
                        summary=(
                            "The README states a fixed test count that no longer "
                            "matches the suite. Prefer describing how to run the "
                            "tests over stating a number that goes stale."
                        ),
                        claimed=f"{claimed} tests",
                        actual=f"{actual} test functions found under tests/",
                        sources=["README.md"],
                    )
                )
