"""Structured failure intelligence, stack trace normalization, and anti-pattern learning."""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class FailureEpisode:
    """Structured capture of a failed approach or error signature."""

    task_id: str
    task_text: str
    error_message: str
    failure_signature: str
    attempted_approach: str
    command_or_tool: str | None = None
    normalized_trace: str | None = None
    root_cause: str | None = None
    affected_files: list[str] = field(default_factory=list)
    attempted_fix: str | None = None
    fix_worked: bool = False
    why_worked_or_failed: str | None = None
    commit_sha: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FailureNormalizer:
    """Normalizes noisy stack traces and generates reproducible failure signatures."""

    @classmethod
    def normalize_stack_trace(cls, raw_trace: str) -> str:
        """Strip volatile file system paths, line numbers, and hex pointers from stack traces."""
        if not raw_trace:
            return ""

        text = raw_trace

        # Replace Windows and Unix file paths with canonical <FILE>
        text = re.sub(r'[A-Za-z]:\\[^\n:]+\\([A-Za-z0-9_]+\.[a-zA-Z0-9]+)', r'<PATH>/\1', text)
        text = re.sub(r'/[\w\.\-]+/[\w\.\-/]+/([A-Za-z0-9_]+\.[a-zA-Z0-9]+)', r'<PATH>/\1', text)

        # Replace line numbers: "line 123" -> "line <LINE>"
        text = re.sub(r'\bline \d+\b', 'line <LINE>', text)
        text = re.sub(r':\d+:', ':<LINE>:', text)

        # Replace hexadecimal memory addresses (e.g. 0x000001B840BF0230)
        text = re.sub(r'0x[0-9a-fA-F]{4,16}', '<HEX>', text)

        # Replace UUIDs
        text = re.sub(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '<UUID>', text)

        # Normalize whitespace
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    @classmethod
    def compute_signature(cls, error_type: str, normalized_trace: str) -> str:
        """Compute stable SHA-256 fingerprint for grouping recurring failures."""
        clean_err = (error_type or "UnknownError").strip()
        # Use first 8 lines of normalized trace for signature stability
        trace_summary = "\n".join(normalized_trace.splitlines()[:8])
        raw_sig = f"{clean_err}::{trace_summary}"
        return hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()[:16]


class FailureIntelligenceEngine:
    """Tracks failure episodes, groups recurring bugs, and converts verified fixes into durable L4 memories."""

    def __init__(self) -> None:
        self.normalizer = FailureNormalizer()

    def process_test_failure(
        self,
        task_id: str,
        task_text: str,
        error_text: str,
        stack_trace: str | None = None,
        attempted_approach: str = "Automated test execution",
        affected_files: list[str] | None = None,
    ) -> FailureEpisode:
        """Construct a structured FailureEpisode with normalized fingerprint."""
        norm_trace = self.normalizer.normalize_stack_trace(stack_trace or error_text)
        # Extract exception class name if available (e.g. TypeError, AssertionError, ValueError)
        exc_match = re.search(r'([A-Za-z0-9_]+Error|[A-Za-z0-9_]+Exception|AssertionError):', error_text)
        exc_type = exc_match.group(1) if exc_match else "TestFailure"

        sig = self.normalizer.compute_signature(exc_type, norm_trace)

        return FailureEpisode(
            task_id=task_id,
            task_text=task_text,
            error_message=error_text[:500],
            failure_signature=sig,
            attempted_approach=attempted_approach,
            normalized_trace=norm_trace,
            affected_files=affected_files or [],
            fix_worked=False,
        )
