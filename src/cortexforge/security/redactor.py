"""Secret redaction and untrusted content sanitization for CortexForge."""

import re

# Common secret and token regex patterns
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            re.MULTILINE,
        ),
    ),
    (
        "OPENAI_KEY",
        re.compile(r"sk-(?:proj-|ant-|live-)?[a-zA-Z0-9_\-]{20,}", re.ASCII),
    ),
    (
        "AWS_KEY",
        re.compile(r"(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}", re.ASCII),
    ),
    (
        "GITHUB_TOKEN",
        re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,}|github_pat_[a-zA-Z0-9_]{40,}", re.ASCII),
    ),
    (
        "JWT_TOKEN",
        re.compile(r"eyJ[a-zA-Z0-9_\-]{5,}\.eyJ[a-zA-Z0-9_\-]{5,}\.[a-zA-Z0-9_\-]+", re.ASCII),
    ),
    (
        "SLACK_TOKEN",
        re.compile(r"xox[baprs]-[0-9]{10,}-[a-zA-Z0-9]{24,}", re.ASCII),
    ),
    (
        "GENERIC_PASSWORD",
        re.compile(
            r"""(?i)(?:api_key|apikey|secret|password|passwd|auth_token|access_token)\s*[:=]\s*['"]([a-zA-Z0-9!@#$%^&*()_+\-=\[\]{}|;:,.<>?/~`]{8,})['"]""",
            re.ASCII,
        ),
    ),
]

# Injection delimiters commonly used to break out of agent instruction boundaries
PROMPT_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("SYSTEM_PROMPT_DELIMITER", re.compile(r"<\|im_start\|>|<\|im_end\|>|\[SYSTEM\]|\[HUMAN\]|\[ASSISTANT\]", re.IGNORECASE)),
    ("INSTRUCTION_OVERRIDE", re.compile(r"(?i)(?:ignore\s+(?:all\s+)?previous\s+instructions|disregard\s+(?:all\s+)?prior\s+rules)")),
]


class SecretRedactor:
    """Scans and redacts credentials, private keys, API keys, and injection attempts."""

    @classmethod
    def redact_secrets(cls, text: str) -> str:
        """Redact secrets and sensitive tokens from source code or memory content."""
        if not text:
            return ""

        redacted = text
        for name, pattern in SECRET_PATTERNS:
            if name == "GENERIC_PASSWORD":
                # Only redact the matched capture group (the actual password)
                redacted = pattern.sub(lambda m: m.group(0).replace(m.group(1), "[REDACTED_CREDENTIAL]"), redacted)
            else:
                redacted = pattern.sub(f"[REDACTED_{name}]", redacted)

        return redacted

    @classmethod
    def neutralize_injections(cls, text: str) -> str:
        """Neutralize malicious instruction override tags from untrusted files."""
        if not text:
            return ""

        sanitized = text
        for name, pattern in PROMPT_INJECTION_PATTERNS:
            sanitized = pattern.sub(f"[NEUTRALIZED_{name}]", sanitized)

        return sanitized

    @classmethod
    def sanitize(cls, text: str) -> str:
        """Apply full secret redaction and injection neutralization."""
        return cls.neutralize_injections(cls.redact_secrets(text))


def sanitize_text(text: str) -> str:
    """Convenience helper for full sanitization."""
    return SecretRedactor.sanitize(text)
