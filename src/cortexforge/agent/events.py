"""Canonical agent event models and multi-vendor ingestion adapters."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class CanonicalEventType(str, Enum):
    """Universal canonical lifecycle and observation events across all AI coding agents."""

    TASK_STARTED = "TASK_STARTED"
    CONTEXT_REQUESTED = "CONTEXT_REQUESTED"
    MEMORY_RETRIEVED = "MEMORY_RETRIEVED"
    FILE_READ = "FILE_READ"
    TOOL_CALLED = "TOOL_CALLED"
    FILE_CHANGED = "FILE_CHANGED"
    TEST_STARTED = "TEST_STARTED"
    TEST_FAILED = "TEST_FAILED"
    TEST_PASSED = "TEST_PASSED"
    COMMIT_CREATED = "COMMIT_CREATED"
    REVIEW_CREATED = "REVIEW_CREATED"
    TASK_COMPLETED = "TASK_COMPLETED"


class CanonicalEvent(BaseModel):
    """Normalized, vendor-neutral event representation."""

    task_id: str
    event_type: CanonicalEventType
    source: str = "generic_mcp"  # claude_code, gemini_antigravity, cursor, mcp, cli
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BaseAgentAdapter(ABC):
    """Abstract base adapter for normalizing vendor-specific agent hooks into CanonicalEvent."""

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Name of the AI agent runtime adapter."""

    def extract_canonical_event_type(
        self, raw_data: dict[str, Any]
    ) -> CanonicalEventType | None:
        """Check if an explicit canonical event_type is already supplied."""
        val = raw_data.get("event_type") or raw_data.get("canonical_event_type")
        if val:
            if isinstance(val, CanonicalEventType):
                return val
            try:
                return CanonicalEventType(str(val).upper())
            except ValueError:
                pass
        return None

    @abstractmethod
    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        """Translate runtime-specific event format into CanonicalEvent."""


class GenericMCPAdapter(BaseAgentAdapter):
    """Adapter for MCP standard tool calls and notifications."""

    @property
    def adapter_name(self) -> str:
        return "mcp"

    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        ev_type = self.extract_canonical_event_type(raw_data)
        if ev_type is None:
            event_name = str(raw_data.get("type") or "TOOL_CALLED").upper()
            try:
                ev_type = CanonicalEventType(event_name)
            except ValueError:
                ev_type = CanonicalEventType.TOOL_CALLED

        return CanonicalEvent(
            task_id=task_id,
            event_type=ev_type,
            source=self.adapter_name,
            payload=raw_data.get("payload") or raw_data,
        )


class ClaudeCodeAdapter(BaseAgentAdapter):
    """Adapter for Claude Code hook payloads."""

    @property
    def adapter_name(self) -> str:
        return "claude_code"

    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        ev_type = self.extract_canonical_event_type(raw_data)
        if ev_type is None:
            hook_name = str(
                raw_data.get("hook_name") or raw_data.get("event") or ""
            ).lower()
            if "pre_tool" in hook_name or "tool" in hook_name:
                ev_type = CanonicalEventType.TOOL_CALLED
            elif (
                "post_edit" in hook_name
                or "file_modified" in hook_name
                or "edit" in hook_name
                or "write" in hook_name
            ):
                ev_type = CanonicalEventType.FILE_CHANGED
            elif "test_fail" in hook_name or "fail" in hook_name:
                ev_type = CanonicalEventType.TEST_FAILED
            elif "test_pass" in hook_name or "pass" in hook_name:
                ev_type = CanonicalEventType.TEST_PASSED
            elif (
                "finish" in hook_name or "stop" in hook_name or "complete" in hook_name
            ):
                ev_type = CanonicalEventType.TASK_COMPLETED
            elif "start" in hook_name or "init" in hook_name:
                ev_type = CanonicalEventType.TASK_STARTED
            else:
                ev_type = CanonicalEventType.TOOL_CALLED

        return CanonicalEvent(
            task_id=task_id,
            event_type=ev_type,
            source=self.adapter_name,
            payload=raw_data.get("payload") or raw_data,
        )


class AntigravityAdapter(BaseAgentAdapter):
    """Adapter for Google DeepMind Antigravity / Gemini coding agent workflows."""

    @property
    def adapter_name(self) -> str:
        return "gemini_antigravity"

    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        ev_type = self.extract_canonical_event_type(raw_data)
        if ev_type is None:
            step_type = str(
                raw_data.get("type") or raw_data.get("step_type") or ""
            ).upper()
            step_type_lower = step_type.lower()
            status_lower = str(raw_data.get("status") or "").lower()

            if step_type in ("USER_INPUT", "TASK_STARTED", "INIT"):
                ev_type = CanonicalEventType.TASK_STARTED
            elif "file" in raw_data and (
                "changed" in step_type_lower
                or "write" in step_type_lower
                or "edit" in step_type_lower
            ):
                ev_type = CanonicalEventType.FILE_CHANGED
            elif "test" in step_type_lower and (
                "fail" in step_type_lower
                or "fail" in status_lower
                or status_lower == "error"
            ):
                ev_type = CanonicalEventType.TEST_FAILED
            elif "test" in step_type_lower and (
                "pass" in step_type_lower
                or "pass" in status_lower
                or status_lower == "done"
            ):
                ev_type = CanonicalEventType.TEST_PASSED
            elif step_type in ("TASK_COMPLETED", "COMPLETE", "FINISHED"):
                ev_type = CanonicalEventType.TASK_COMPLETED
            elif (
                step_type in ("TOOL_CALLED", "TOOL_CALL")
                or "tool_call" in raw_data
                or "tool_name" in raw_data
                or "tool_calls" in raw_data
            ):
                ev_type = CanonicalEventType.TOOL_CALLED
            else:
                ev_type = CanonicalEventType.TOOL_CALLED

        return CanonicalEvent(
            task_id=task_id,
            event_type=ev_type,
            source=self.adapter_name,
            payload=raw_data.get("payload") or raw_data,
        )


class CursorAdapter(BaseAgentAdapter):
    """Adapter for Cursor IDE editor hooks and terminal events."""

    @property
    def adapter_name(self) -> str:
        return "cursor"

    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        ev_type = self.extract_canonical_event_type(raw_data)
        if ev_type is None:
            action = str(raw_data.get("action") or raw_data.get("event") or "").lower()
            output = str(raw_data.get("output") or "").lower()
            if "edit" in action or "save" in action or "modify" in action:
                ev_type = CanonicalEventType.FILE_CHANGED
            elif "test" in action and (
                "fail" in action or "fail" in output or "error" in output
            ):
                ev_type = CanonicalEventType.TEST_FAILED
            elif "test" in action and (
                "pass" in action or "ok" in output or "success" in output
            ):
                ev_type = CanonicalEventType.TEST_PASSED
            elif "terminal" in action and ("fail" in output or "error" in output):
                ev_type = CanonicalEventType.TEST_FAILED
            elif "finish" in action or "complete" in action:
                ev_type = CanonicalEventType.TASK_COMPLETED
            elif "start" in action or "init" in action:
                ev_type = CanonicalEventType.TASK_STARTED
            else:
                ev_type = CanonicalEventType.TOOL_CALLED

        return CanonicalEvent(
            task_id=task_id,
            event_type=ev_type,
            source=self.adapter_name,
            payload=raw_data.get("payload") or raw_data,
        )


class CLIAdapter(BaseAgentAdapter):
    """Adapter for CortexForge CLI commands and local dev hooks."""

    @property
    def adapter_name(self) -> str:
        return "cli"

    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        ev_type = self.extract_canonical_event_type(raw_data)
        if ev_type is None:
            ev_type_str = str(raw_data.get("type") or "TOOL_CALLED").upper()
            try:
                ev_type = CanonicalEventType(ev_type_str)
            except ValueError:
                ev_type = CanonicalEventType.TOOL_CALLED

        return CanonicalEvent(
            task_id=task_id,
            event_type=ev_type,
            source=self.adapter_name,
            payload=raw_data.get("payload") or raw_data,
        )


class EventAdapterRegistry:
    """Central registry resolving runtime adapter by agent source name."""

    _adapters: ClassVar[dict[str, BaseAgentAdapter]] = {
        "mcp": GenericMCPAdapter(),
        "generic_mcp": GenericMCPAdapter(),
        "claude_code": ClaudeCodeAdapter(),
        "claude": ClaudeCodeAdapter(),
        "gemini_antigravity": AntigravityAdapter(),
        "antigravity": AntigravityAdapter(),
        "gemini": AntigravityAdapter(),
        "cursor": CursorAdapter(),
        "cli": CLIAdapter(),
    }

    @classmethod
    def get_adapter(cls, source_name: str | None) -> BaseAgentAdapter:
        if not source_name:
            return cls._adapters["mcp"]
        norm = source_name.lower().strip()
        return cls._adapters.get(norm, cls._adapters["mcp"])
