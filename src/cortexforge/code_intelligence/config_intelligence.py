"""Configuration, schema and contract indexing (specification section 14).

Code is not the whole project. A service's behaviour is determined as much by its
Docker image, its CI pipeline, its database migrations and its API contract as by
its Python. A memory layer that indexes only `.py` files cannot notice that a
required environment variable disappeared, that an endpoint was removed from the
OpenAPI spec, or that a migration dropped a column something depends on.

The evidence types for this already existed -- `CONFIG`, `SCHEMA`, `API_CONTRACT`
-- and the verification engine already had a policy that checks them. Nothing
produced them, which made both an unused mechanism. This module is the producer.

Configuration artifacts are indexed as `CodeEntity` rows so they participate in
the same change-impact and reconciliation machinery as code. That is deliberate:
a schema change and a signature change should reach memory through one pipeline,
not two that drift apart.

Parsing is intentionally shallow. The goal is to know *what keys exist where* so
a claim can be anchored to one and re-checked when it moves, not to model the
semantics of every configuration format. A parser that tried to understand
Kubernetes manifests would be wrong in ways nobody would notice.
"""

import hashlib
import json
import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cortexforge.cognition.epistemics import EvidenceType

logger = logging.getLogger(__name__)


# Artifact kinds, mapped to the evidence type a claim about them would carry.
KIND_ENV = "env"
KIND_DOCKER = "docker"
KIND_COMPOSE = "compose"
KIND_CI = "ci"
KIND_YAML = "yaml"
KIND_TOML = "toml"
KIND_JSON = "json"
KIND_OPENAPI = "openapi"
KIND_MIGRATION = "migration"
KIND_DEPENDENCY = "dependency"

EVIDENCE_TYPE_FOR_KIND: dict[str, str] = {
    KIND_ENV: EvidenceType.CONFIG.value,
    KIND_DOCKER: EvidenceType.CONFIG.value,
    KIND_COMPOSE: EvidenceType.CONFIG.value,
    KIND_CI: EvidenceType.CONFIG.value,
    KIND_YAML: EvidenceType.CONFIG.value,
    KIND_TOML: EvidenceType.CONFIG.value,
    KIND_JSON: EvidenceType.CONFIG.value,
    KIND_OPENAPI: EvidenceType.API_CONTRACT.value,
    KIND_MIGRATION: EvidenceType.SCHEMA.value,
    KIND_DEPENDENCY: EvidenceType.CONFIG.value,
}

# Filenames recognised regardless of extension.
_EXACT_NAMES: dict[str, str] = {
    "dockerfile": KIND_DOCKER,
    "docker-compose.yml": KIND_COMPOSE,
    "docker-compose.yaml": KIND_COMPOSE,
    "compose.yml": KIND_COMPOSE,
    "compose.yaml": KIND_COMPOSE,
    "pyproject.toml": KIND_DEPENDENCY,
    "package.json": KIND_DEPENDENCY,
    "requirements.txt": KIND_DEPENDENCY,
    "cargo.toml": KIND_DEPENDENCY,
    "go.mod": KIND_DEPENDENCY,
    "alembic.ini": KIND_CI,
    "openapi.json": KIND_OPENAPI,
    "openapi.yaml": KIND_OPENAPI,
    "openapi.yml": KIND_OPENAPI,
}

_ENV_SUFFIXES = (".env", ".env.example", ".env.template", ".env.sample")

# Generated and vendored artifacts. Indexing a lockfile would produce thousands
# of keys nobody would ever anchor a claim to, and indexing this system's own
# benchmark output would let its results become evidence for its own memories.
_GENERATED_FILENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
        "tsconfig.tsbuildinfo",
    }
)

_GENERATED_DIR_MARKERS: tuple[str, ...] = (
    "/benchmarks/results/",
    "/coverage/",
    "/htmlcov/",
    "/.pytest_cache/",
)

_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_DOCKER_DIRECTIVE = re.compile(
    r"^\s*(FROM|ENV|EXPOSE|ARG|CMD|ENTRYPOINT|WORKDIR|USER|VOLUME|HEALTHCHECK)\s+(.*)$",
    re.IGNORECASE,
)
_YAML_TOP_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*):")
_YAML_NESTED_KEY = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_\-]*):")
_MIGRATION_OP = re.compile(
    r"op\.(create_table|drop_table|add_column|drop_column|create_index|drop_index|"
    r"alter_column|create_unique_constraint|create_check_constraint)\(\s*['\"]([^'\"]+)['\"]"
)


@dataclass
class ConfigKey:
    """One addressable setting inside a configuration artifact."""

    name: str
    line: int
    value_preview: str | None = None


@dataclass
class ConfigArtifact:
    """A parsed configuration, schema or contract file."""

    file_path: str
    kind: str
    keys: list[ConfigKey] = field(default_factory=list)
    content_hash: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def evidence_type(self) -> str:
        """The evidence type a claim grounded in this artifact carries."""
        return EVIDENCE_TYPE_FOR_KIND.get(self.kind, EvidenceType.CONFIG.value)

    @property
    def qualified_name(self) -> str:
        return self.file_path


def classify(file_path: str) -> str | None:
    """Identify what kind of configuration artifact a path is, if any.

    Returns ``None`` for anything unrecognised. Guessing at unknown formats
    would produce entities nothing can verify.
    """
    normalized = file_path.replace("\\", "/")
    name = os.path.basename(normalized).lower()

    if name in _GENERATED_FILENAMES:
        return None
    if any(marker in f"/{normalized}" for marker in _GENERATED_DIR_MARKERS):
        return None

    if name in _EXACT_NAMES:
        return _EXACT_NAMES[name]
    if name.startswith("dockerfile"):
        return KIND_DOCKER
    if name.endswith(_ENV_SUFFIXES) or name == ".env":
        return KIND_ENV

    # CI definitions are identified by location, since their filenames are free-form.
    if "/.github/workflows/" in f"/{normalized}" and name.endswith((".yml", ".yaml")):
        return KIND_CI
    if name in (
        "gitlab-ci.yml",
        ".gitlab-ci.yml",
        "azure-pipelines.yml",
        "jenkinsfile",
    ):
        return KIND_CI

    # Alembic revisions live under a migrations/ or versions/ directory by
    # convention. They are Python *and* schema, and are classified as schema
    # because that is the part a memory would depend on.
    in_migration_dir = (
        "/migrations/" in f"/{normalized}" or "/versions/" in f"/{normalized}"
    )
    if in_migration_dir and name.endswith(".py"):
        return KIND_MIGRATION

    extension = os.path.splitext(name)[1]
    if extension in (".yml", ".yaml"):
        return KIND_YAML
    if extension == ".toml":
        return KIND_TOML
    if extension == ".json":
        return KIND_JSON
    return None


class ConfigIntelligenceProvider:
    """Extracts addressable keys from configuration, schema and contract files."""

    def parse(self, file_path: str, content: bytes) -> ConfigArtifact | None:
        """Parse a configuration artifact into its addressable keys.

        Returns ``None`` for files this provider does not recognise, so callers
        can distinguish "not a configuration file" from "a configuration file
        with no keys".
        """
        kind = classify(file_path)
        if kind is None:
            return None

        try:
            text = content.decode("utf-8", errors="ignore")
        except (UnicodeDecodeError, AttributeError):
            return None

        artifact = ConfigArtifact(
            file_path=file_path.replace("\\", "/"),
            kind=kind,
            content_hash=hashlib.sha256(content).hexdigest(),
        )

        parser = {
            KIND_ENV: self._parse_env,
            KIND_DOCKER: self._parse_dockerfile,
            KIND_COMPOSE: self._parse_yaml,
            KIND_CI: self._parse_yaml,
            KIND_YAML: self._parse_yaml,
            KIND_TOML: self._parse_toml,
            KIND_JSON: self._parse_json,
            KIND_OPENAPI: self._parse_openapi,
            KIND_MIGRATION: self._parse_migration,
            KIND_DEPENDENCY: self._parse_dependency,
        }[kind]

        try:
            parser(text, artifact)
        except Exception as exc:
            # A malformed configuration file is a fact about the repository, not
            # a reason to abort a scan. The artifact is still recorded so that a
            # claim can be anchored to the file even if its keys are unreadable.
            logger.debug("Could not fully parse %s (%s): %s", file_path, kind, exc)
            artifact.detail["parse_error"] = str(exc)[:200]

        return artifact

    # ------------------------------------------------------------- parsers

    @staticmethod
    def _parse_env(text: str, artifact: ConfigArtifact) -> None:
        """Environment variable declarations, with values withheld.

        Values are deliberately not captured: a `.env` file is where credentials
        live, and an indexer that stored them would be exfiltrating secrets into
        the memory store (section 41). The key's presence is what a claim anchors
        to; its value is not.
        """
        for number, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            match = _ENV_ASSIGNMENT.match(line)
            if match:
                artifact.keys.append(ConfigKey(name=match.group(1), line=number))

    @staticmethod
    def _parse_dockerfile(text: str, artifact: ConfigArtifact) -> None:
        """Docker directives, keyed by instruction and argument."""
        for number, line in enumerate(text.splitlines(), start=1):
            match = _DOCKER_DIRECTIVE.match(line)
            if not match:
                continue
            directive = match.group(1).upper()
            argument = match.group(2).strip()
            # ENV assignments are named individually so a claim can depend on one
            # variable rather than on "the Dockerfile".
            if directive == "ENV":
                variable = argument.split("=")[0].split()[0] if argument else ""
                name = f"ENV:{variable}" if variable else "ENV"
            else:
                name = directive
            artifact.keys.append(
                ConfigKey(name=name, line=number, value_preview=argument[:120] or None)
            )

    @staticmethod
    def _parse_yaml(text: str, artifact: ConfigArtifact) -> None:
        """Top-level and second-level YAML keys, by line.

        A structural scan rather than a YAML parse: it needs no dependency, it
        cannot fail on a template placeholder that is not valid YAML, and knowing
        which keys exist at which line is all an anchor needs.
        """
        current_top: str | None = None
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue

            top = _YAML_TOP_KEY.match(line)
            if top:
                current_top = top.group(1)
                artifact.keys.append(ConfigKey(name=current_top, line=number))
                continue

            nested = _YAML_NESTED_KEY.match(line)
            if nested and current_top and len(nested.group(1)) <= 4:
                artifact.keys.append(
                    ConfigKey(name=f"{current_top}.{nested.group(2)}", line=number)
                )

    @staticmethod
    def _parse_toml(text: str, artifact: ConfigArtifact) -> None:
        data = tomllib.loads(text)
        for section, value in data.items():
            artifact.keys.append(
                ConfigKey(name=section, line=_find_line(text, section))
            )
            if isinstance(value, dict):
                for key in value:
                    artifact.keys.append(
                        ConfigKey(name=f"{section}.{key}", line=_find_line(text, key))
                    )

    @staticmethod
    def _parse_json(text: str, artifact: ConfigArtifact) -> None:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in data:
                artifact.keys.append(ConfigKey(name=key, line=_find_line(text, key)))

    @staticmethod
    def _parse_openapi(text: str, artifact: ConfigArtifact) -> None:
        """API contract paths and operations.

        Each path becomes an addressable key, so a memory can be grounded in one
        endpoint and go stale when that endpoint is removed -- which is the whole
        point of treating a contract as evidence.
        """
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            # A YAML OpenAPI document still yields its paths structurally.
            ConfigIntelligenceProvider._parse_yaml(text, artifact)
            return

        artifact.detail["openapi_version"] = document.get("openapi") or document.get(
            "swagger"
        )
        for path, operations in (document.get("paths") or {}).items():
            artifact.keys.append(ConfigKey(name=path, line=_find_line(text, path)))
            if isinstance(operations, dict):
                for method in operations:
                    if method.lower() in (
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                        "head",
                        "options",
                    ):
                        artifact.keys.append(
                            ConfigKey(
                                name=f"{method.upper()} {path}",
                                line=_find_line(text, path),
                            )
                        )

    @staticmethod
    def _parse_migration(text: str, artifact: ConfigArtifact) -> None:
        """Schema operations performed by a migration.

        A memory grounded in "the users table has an email column" should be
        reachable from the migration that added it and disturbed by the one that
        drops it.
        """
        for match in _MIGRATION_OP.finditer(text):
            operation, target = match.group(1), match.group(2)
            artifact.keys.append(
                ConfigKey(
                    name=f"{operation}:{target}",
                    line=text[: match.start()].count("\n") + 1,
                )
            )
        revision = re.search(
            r"^revision(?:\s*:\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE
        )
        if revision:
            artifact.detail["revision"] = revision.group(1)

    @staticmethod
    def _parse_dependency(text: str, artifact: ConfigArtifact) -> None:
        """Declared dependencies, so a dependency change can reach memory."""
        if artifact.file_path.endswith("package.json"):
            document = json.loads(text)
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                for name in document.get(section) or {}:
                    artifact.keys.append(
                        ConfigKey(name=f"{section}.{name}", line=_find_line(text, name))
                    )
            return

        if artifact.file_path.endswith(".toml"):
            document = tomllib.loads(text)
            project = document.get("project", {})
            for requirement in project.get("dependencies", []) or []:
                package = re.split(r"[<>=!~\[ ]", str(requirement))[0]
                artifact.keys.append(
                    ConfigKey(
                        name=f"dependencies.{package}", line=_find_line(text, package)
                    )
                )
            for group, requirements in (
                project.get("optional-dependencies") or {}
            ).items():
                for requirement in requirements:
                    package = re.split(r"[<>=!~\[ ]", str(requirement))[0]
                    artifact.keys.append(
                        ConfigKey(
                            name=f"optional-dependencies.{group}.{package}",
                            line=_find_line(text, package),
                        )
                    )
            return

        # requirements.txt and similar: one requirement per line.
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "-")):
                continue
            package = re.split(r"[<>=!~\[ ]", stripped)[0]
            if package:
                artifact.keys.append(
                    ConfigKey(name=f"dependencies.{package}", line=number)
                )


def _find_line(text: str, needle: str) -> int:
    """First line mentioning a key, for anchoring. Best-effort by design."""
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    return 1


def discover_config_files(root_path: str, ignored_dirs: set[str]) -> list[str]:
    """Find configuration artifacts under a repository root.

    Dot-directories are pruned like the code scanner does, with one exception:
    `.github` holds CI definitions, which are exactly the kind of configuration
    this exists to index.
    """
    canonical_root = os.path.realpath(root_path)
    found: list[str] = []

    for dirpath, dirnames, filenames in os.walk(canonical_root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in ignored_dirs
            and (not name.startswith(".") or name == ".github")
        ]
        for filename in filenames:
            relative = os.path.relpath(
                os.path.join(dirpath, filename), canonical_root
            ).replace("\\", "/")
            if classify(relative) is not None:
                found.append(relative)

    return sorted(found)


def read_artifact(root_path: str, relative_path: str) -> ConfigArtifact | None:
    """Parse one configuration artifact from disk."""
    absolute = Path(root_path) / relative_path
    try:
        content = absolute.read_bytes()
    except OSError:
        return None
    return ConfigIntelligenceProvider().parse(relative_path, content)
