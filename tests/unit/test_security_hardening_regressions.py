"""Adversarial regression test suite for final security hardening pass.

Enforces:
1. Evidence grounding cannot escape project workspace (relative traversal, absolute path,
   UNC, symlink escape, similar-prefix directory escape).
2. Secret redaction cannot be bypassed by truncated private keys, compact JWTs, or Bearer tokens.
"""

import os

import pytest

from cortexforge.cognition.authority import Authority
from cortexforge.cognition.promotion import evaluate_memory_promotion
from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.security.redactor import SecretRedactor

# =============================================================================
# 1. EVIDENCE GROUNDING PATH TRAVERSAL & WORKSPACE ESCAPE REGRESSIONS
# =============================================================================


def test_evidence_path_traversal_relative_escape_rejected(tmp_path):
    """Evidence with '../' escaping project workspace must NEVER be promoted to ACTIVE."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    secret_file = outside / "secret.py"
    secret_file.write_text("API_KEY = 'secret'\n", encoding="utf-8")

    proj = Project(name="TestProj", local_path=str(ws), status="READY")
    rel_path = os.path.relpath(
        str(secret_file), str(ws)
    )  # e.g. ../outside_dir/secret.py

    payload = MemoryCreate(
        title="Relative Traversal Rule",
        content="Evidence points outside workspace",
        summary="Traversal",
        memory_type="FACT",
        source_type="code",
        evidence=[
            MemoryEvidenceCreate(
                file_path=rel_path, source_type="code", line_start=1, line_end=1
            )
        ],
    )

    decision = evaluate_memory_promotion(payload, proj, Authority.CODE_VERIFIED)
    assert decision.eligible is False, (
        f"Expected eligible=False for relative traversal {rel_path}"
    )
    assert decision.initial_status != "ACTIVE", (
        f"Status must not be ACTIVE for traversal {rel_path}"
    )
    assert decision.verified_at is None


def test_evidence_path_absolute_escape_rejected(tmp_path):
    """Evidence with absolute path outside project workspace must NEVER be promoted to ACTIVE."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    secret_file = outside / "host_secrets.py"
    secret_file.write_text("HOST_SECRET = True\n", encoding="utf-8")

    proj = Project(name="TestProj", local_path=str(ws), status="READY")

    payload = MemoryCreate(
        title="Absolute Path Escape",
        content="Evidence uses absolute path outside workspace",
        summary="Escape",
        memory_type="FACT",
        source_type="code",
        evidence=[
            MemoryEvidenceCreate(
                file_path=str(secret_file), source_type="code", line_start=1, line_end=1
            )
        ],
    )

    decision = evaluate_memory_promotion(payload, proj, Authority.CODE_VERIFIED)
    assert decision.eligible is False
    assert decision.initial_status != "ACTIVE"
    assert decision.verified_at is None


def test_evidence_similar_prefix_directory_rejected(tmp_path):
    """Evidence in /workspace/project-secret must not be valid for /workspace/project."""
    ws = tmp_path / "project"
    ws.mkdir()
    similar_ws = tmp_path / "project-secret"
    similar_ws.mkdir()
    fake_file = similar_ws / "logic.py"
    fake_file.write_text("def fake(): pass\n", encoding="utf-8")

    proj = Project(name="TestProj", local_path=str(ws), status="READY")
    rel_path = os.path.relpath(str(fake_file), str(ws))

    payload = MemoryCreate(
        title="Similar Prefix Escape",
        content="Evidence in similar prefix sibling directory",
        summary="Sibling",
        memory_type="FACT",
        source_type="code",
        evidence=[
            MemoryEvidenceCreate(
                file_path=rel_path, source_type="code", line_start=1, line_end=1
            )
        ],
    )

    decision = evaluate_memory_promotion(payload, proj, Authority.CODE_VERIFIED)
    assert decision.eligible is False
    assert decision.initial_status != "ACTIVE"


def test_evidence_symlink_escape_rejected(tmp_path):
    """A symlink or junction inside workspace pointing to an outside file must NOT be accepted as workspace evidence."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target_file = outside / "external_target.py"
    target_file.write_text("EXTERNAL = 1\n", encoding="utf-8")

    link_path = ws / "symlink_escape.py"
    rel_evidence_path = "symlink_escape.py"
    created = False
    try:
        os.symlink(str(target_file), str(link_path))
        created = True
    except (OSError, NotImplementedError):
        # On Windows without SeCreateSymbolicLinkPrivilege, test directory junction
        try:
            import _winapi

            junction_dir = ws / "linked_outside"
            _winapi.CreateJunction(str(outside), str(junction_dir))
            rel_evidence_path = "linked_outside/external_target.py"
            created = True
        except (OSError, ImportError):
            created = False

    if not created:
        pytest.skip(
            "Symlinks and junctions not supported on this platform/environment without privilege"
        )

    proj = Project(name="TestProj", local_path=str(ws), status="READY")

    payload = MemoryCreate(
        title="Symlink Escape Rule",
        content="Symlink points outside workspace",
        summary="Symlink",
        memory_type="FACT",
        source_type="code",
        evidence=[
            MemoryEvidenceCreate(
                file_path=rel_evidence_path,
                source_type="code",
                line_start=1,
                line_end=1,
            )
        ],
    )

    decision = evaluate_memory_promotion(payload, proj, Authority.CODE_VERIFIED)
    assert decision.eligible is False
    assert decision.initial_status != "ACTIVE"
    assert decision.verified_at is None
    assert decision.authority != Authority.CODE_VERIFIED


def test_evidence_legitimate_nested_path_accepted(tmp_path):
    """Legitimate code evidence inside workspace subdirectories must be ACCEPTED."""
    ws = tmp_path / "workspace"
    subdir = ws / "src" / "pkg"
    subdir.mkdir(parents=True)
    real_file = subdir / "service.py"
    real_file.write_text("class Service:\n    pass\n", encoding="utf-8")

    proj = Project(name="TestProj", local_path=str(ws), status="READY")

    payload = MemoryCreate(
        title="Legitimate Service Decision",
        content="Service is validated",
        summary="Valid",
        memory_type="FACT",
        source_type="code",
        evidence=[
            MemoryEvidenceCreate(
                file_path="src/pkg/service.py",
                source_type="code",
                line_start=1,
                line_end=2,
            )
        ],
    )

    decision = evaluate_memory_promotion(payload, proj, Authority.CODE_VERIFIED)
    assert decision.eligible is True
    assert decision.initial_status == "ACTIVE"
    assert decision.verified_at is not None


# =============================================================================
# 2. SECRET REDACTION BLINDSPOT REGRESSIONS
# =============================================================================


def test_redact_private_key_header_without_end_marker():
    """Header-only or truncated private keys must be redacted."""
    headers = [
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
    ]
    for h in headers:
        text = f"Accidental log: {h}\nPartial key data MIIEowIBAAKCAQEA..."
        redacted = SecretRedactor.redact_secrets(text)
        assert h not in redacted, f"Header {h} leaked in redacted text: {redacted}"
        assert "[REDACTED_PRIVATE_KEY]" in redacted


def test_redact_compact_and_minimal_jwts():
    """JWTs with minimal or non-'eyJ' payload sections must be redacted."""
    tokens = [
        # Standard realistic token with short payload .e30.
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDcSemACt8x4iTMCda8Yhe3iZaWbvV5XKSTbuAn0M",
        # Compact minimal JWT
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        # Minimal payload
        "eyJhbGciOiJub25lIn0.e30.signature123",
    ]
    for tok in tokens:
        text = f"Authorization header contains token: {tok}"
        redacted = SecretRedactor.redact_secrets(text)
        assert tok not in redacted, (
            f"JWT token {tok} leaked in redacted text: {redacted}"
        )
        assert "[REDACTED_JWT_TOKEN]" in redacted


def test_redact_bearer_tokens():
    """HTTP Bearer tokens must be detected and redacted regardless of capitalization."""
    test_cases = [
        ("Bearer abcdefghijklmnop12345", "Bearer [REDACTED_BEARER_TOKEN]"),
        ("bearer abcdefghijklmnop12345", "bearer [REDACTED_BEARER_TOKEN]"),
        (
            "Authorization: Bearer 9876543210zyxwvuts",
            "Authorization: Bearer [REDACTED_BEARER_TOKEN]",
        ),
        (
            "authorization: bearer secret_session_token_12345",
            "authorization: bearer [REDACTED_BEARER_TOKEN]",
        ),
    ]
    for raw, expected in test_cases:
        redacted = SecretRedactor.redact_secrets(raw)
        assert "abcdefghijklmnop12345" not in redacted
        assert "9876543210zyxwvuts" not in redacted
        assert "secret_session_token_12345" not in redacted
        assert "[REDACTED_BEARER_TOKEN]" in redacted


def test_redaction_negative_cases_preserve_legitimate_content():
    """Legitimate non-secret content must NOT be destroyed by aggressive matching."""
    safe_texts = [
        "version.1.2.3 is deployed.",
        "Check domain example.com for routing.",
        "Module path foo.bar.baz is imported.",
        "The Bearer of the sword.",
        "private_key_name = 'my_key_id'",
        "JWT_SECRET_ENV_VAR = 'CONFIG'",
    ]
    for safe in safe_texts:
        redacted = SecretRedactor.redact_secrets(safe)
        assert redacted == safe, f"Legitimate text '{safe}' was mutated to '{redacted}'"


def test_path_security_full_matrix(tmp_path):
    """Explicitly verify PathSecurity matrix cases A through K."""
    from cortexforge.security.path_safety import PathSecurity, PathSecurityError

    root = tmp_path / "project"
    root.mkdir()
    safe_file = root / "safe.py"
    safe_file.write_text("SAFE = True\n")
    sub_dir = root / "subdir"
    sub_dir.mkdir()
    nested_safe = sub_dir / "nested.py"
    nested_safe.write_text("NESTED = True\n")

    # A. project/safe.py => ACCEPT
    assert PathSecurity.safe_resolve(root, "safe.py") == str(safe_file.resolve())
    assert PathSecurity.safe_resolve(root, "subdir/nested.py") == str(
        nested_safe.resolve()
    )

    # B. ../outside.py => REJECT
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, "../outside.py")

    # C. ../../outside.py => REJECT
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, "../../outside.py")

    # D. /etc/passwd => REJECT
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, "/etc/passwd")

    # E. C:\Windows\System32\drivers\etc\hosts => REJECT
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, r"C:\Windows\System32\drivers\etc\hosts")

    # F. \\server\share\secret.txt => REJECT
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, r"\\server\share\secret.txt")

    # H. project/../outside => REJECT
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, "../outside")

    # I. project/subdir/../../outside => REJECT
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, "subdir/../../outside")

    # J. mixed separators => REJECT
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, r"../..\secret.txt")
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, r"..\../secret.txt")

    # K. similar-prefix directory => REJECT
    similar_prefix = tmp_path / "project-secret"
    similar_prefix.mkdir()
    secret_in_sibling = similar_prefix / "secret.py"
    secret_in_sibling.write_text("SECRET")
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, str(secret_in_sibling))


def test_evidence_path_traversal_demotes_authority(tmp_path):
    """Out-of-workspace evidence must demote CODE_VERIFIED authority to prevent authority elevation."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    proj = Project(name="TestProj", local_path=str(ws), status="READY")

    payload = MemoryCreate(
        title="Authority Demotion Rule",
        content="Evidence attempts escape",
        summary="Demote",
        memory_type="FACT",
        source_type="code",
        evidence=[
            MemoryEvidenceCreate(
                file_path="../../system.py",
                source_type="code",
                line_start=1,
                line_end=1,
            )
        ],
    )

    decision = evaluate_memory_promotion(payload, proj, Authority.CODE_VERIFIED)
    assert decision.eligible is False
    assert decision.initial_status == "UNVERIFIED"
    assert decision.authority != Authority.CODE_VERIFIED
    assert decision.authority == Authority.AGENT_OBSERVED
    assert decision.verified_at is None
    assert decision.verified_evidence == []


def test_structured_secret_redaction():
    """Verify recursive scrubbing of dicts, lists, nested metadata, and evidence details."""
    raw_structure = {
        "title": "Config data",
        "nested": {
            "token": "Bearer my_super_secret_token_12345",
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
            "truncated_key": "-----BEGIN EC PRIVATE KEY-----",
            "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.signature123",
        },
        "tags": ["env:prod", "Bearer test_bearer_token_xyz987"],
        "count": 42,
        "is_active": True,
    }

    sanitized = SecretRedactor.sanitize_structure(raw_structure)
    assert sanitized["nested"]["token"] == "Bearer [REDACTED_BEARER_TOKEN]"
    assert sanitized["nested"]["private_key"] == "[REDACTED_PRIVATE_KEY]"
    assert sanitized["nested"]["truncated_key"] == "[REDACTED_PRIVATE_KEY]"
    assert sanitized["nested"]["jwt"] == "[REDACTED_JWT_TOKEN]"
    assert sanitized["tags"][1] == "Bearer [REDACTED_BEARER_TOKEN]"
    # Non-secrets remain unchanged
    assert sanitized["title"] == "Config data"
    assert sanitized["count"] == 42
    assert sanitized["is_active"] is True


def test_multiline_and_multiple_secrets_redaction():
    """Verify multiple secrets in one string, adjacent punctuation, and unicode surrounding text."""
    raw = (
        "🔐 Deployment log: auth header was Authorization: Bearer abcdef1234567890; "
        "backup key was -----BEGIN OPENSSH PRIVATE KEY----- with config id 123.\n"
        "Session JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDcSemACt8x4iTMCda8Yhe3iZaWbvV5XKSTbuAn0M."
    )
    redacted = SecretRedactor.redact_secrets(raw)
    assert "abcdef1234567890" not in redacted
    assert "-----BEGIN OPENSSH PRIVATE KEY-----" not in redacted
    assert "t-IDcSemACt8x4iTMCda8Yhe3iZaWbvV5XKSTbuAn0M" not in redacted
    assert "[REDACTED_BEARER_TOKEN]" in redacted
    assert "[REDACTED_PRIVATE_KEY]" in redacted
    assert "[REDACTED_JWT_TOKEN]" in redacted
    assert "🔐 Deployment log:" in redacted
    assert "with config id 123." in redacted
