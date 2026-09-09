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

    @abstractmethod
    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        """Translate runtime-specific event format into CanonicalEvent."""


class GenericMCPAdapter(BaseAgentAdapter):
    """Adapter for MCP standard tool calls and notifications."""

    @property
    def adapter_name(self) -> str:
        return "mcp"

    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        event_name = (raw_data.get("event_type") or raw_data.get("type") or "TOOL_CALLED").upper()
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
        # Map Claude Code hook names to canonical events
        hook_name = raw_data.get("hook_name", "").lower()
        if "pre_tool" in hook_name or "tool" in hook_name:
            ev_type = CanonicalEventType.TOOL_CALLED
        elif "post_edit" in hook_name or "file_modified" in hook_name:
            ev_type = CanonicalEventType.FILE_CHANGED
        elif "test_fail" in hook_name:
            ev_type = CanonicalEventType.TEST_FAILED
        elif "test_pass" in hook_name:
            ev_type = CanonicalEventType.TEST_PASSED
        elif "finish" in hook_name:
            ev_type = CanonicalEventType.TASK_COMPLETED
        else:
            ev_type = CanonicalEventType.TOOL_CALLED

        return CanonicalEvent(
            task_id=task_id,
            event_type=ev_type,
            source=self.adapter_name,
            payload=raw_data,
        )


class AntigravityAdapter(BaseAgentAdapter):
    """Adapter for Google DeepMind Antigravity / Gemini coding agent workflows."""

    @property
    def adapter_name(self) -> str:
        return "gemini_antigravity"

    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        step_type = raw_data.get("type", "").upper()
        if step_type == "USER_INPUT":
            ev_type = CanonicalEventType.TASK_STARTED
        elif step_type == "TOOL_CALLED" or "tool_call" in raw_data:
            ev_type = CanonicalEventType.TOOL_CALLED
        elif "file" in raw_data and "changed" in step_type:
            ev_type = CanonicalEventType.FILE_CHANGED
        elif "test" in step_type and "fail" in step_type:
            ev_type = CanonicalEventType.TEST_FAILED
        elif "test" in step_type and "pass" in step_type:
            ev_type = CanonicalEventType.TEST_PASSED
        else:
            ev_type = CanonicalEventType.TOOL_CALLED

        return CanonicalEvent(
            task_id=task_id,
            event_type=ev_type,
            source=self.adapter_name,
            payload=raw_data,
        )


class CursorAdapter(BaseAgentAdapter):
    """Adapter for Cursor IDE editor hooks and terminal events."""

    @property
    def adapter_name(self) -> str:
        return "cursor"

    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        action = raw_data.get("action", "").lower()
        if "edit" in action or "save" in action:
            ev_type = CanonicalEventType.FILE_CHANGED
        elif "terminal" in action and "fail" in raw_data.get("output", ""):
            ev_type = CanonicalEventType.TEST_FAILED
        else:
            ev_type = CanonicalEventType.TOOL_CALLED

        return CanonicalEvent(
            task_id=task_id,
            event_type=ev_type,
            source=self.adapter_name,
            payload=raw_data,
        )


class CLIAdapter(BaseAgentAdapter):
    """Adapter for CortexForge CLI commands and local dev hooks."""

    @property
    def adapter_name(self) -> str:
        return "cli"

    def normalize_event(self, raw_data: dict[str, Any], task_id: str) -> CanonicalEvent:
        ev_type_str = raw_data.get("event_type", "TOOL_CALLED").upper()
        try:
            ev_type = CanonicalEventType(ev_type_str)
        except ValueError:
            ev_type = CanonicalEventType.TOOL_CALLED

        return CanonicalEvent(
            task_id=task_id,
            event_type=ev_type,
            source=self.adapter_name,
            payload=raw_data,
        )


class EventAdapterRegistry:
    """Central registry resolving runtime adapter by agent source name."""

    _adapters: ClassVar[dict[str, BaseAgentAdapter]] = {
        "mcp": GenericMCPAdapter(),
        "generic_mcp": GenericMCPAdapter(),
        "claude_code": ClaudeCodeAdapter(),
        "gemini_antigravity": AntigravityAdapter(),
        "cursor": CursorAdapter(),
        "cli": CLIAdapter(),
    }

    @classmethod
    def get_adapter(cls, source_name: str | None) -> BaseAgentAdapter:
        if not source_name:
            return cls._adapters["mcp"]
        norm = source_name.lower().strip()
        return cls._adapters.get(norm, cls._adapters["mcp"])
