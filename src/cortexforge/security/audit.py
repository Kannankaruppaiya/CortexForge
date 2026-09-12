"""Audit logging service for security-sensitive operations and epistemic events."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    """Records server-derived audit events for security and provenance tracking."""

    @staticmethod
    async def record_event(
        session: AsyncSession,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        project_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason: str = "",
        trace_id: str | None = None,
    ) -> AuditLog:
        """Persist an audit log record with server-derived actor identity."""
        log_entry = AuditLog(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            project_id=project_id,
            before=before,
            after=after,
            reason=reason,
            trace_id=trace_id,
        )
        session.add(log_entry)
        await session.flush()
        return log_entry

    @classmethod
    async def record(
        cls,
        db_session: AsyncSession,
        action: str,
        target_type: str,
        target_id: str | None = None,
        user_id: str | None = None,
        actor_type: str = "USER",
        actor_id: str | None = None,
        project_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Convenience method to record server-derived audit events."""
        effective_actor = f"{actor_type}:{user_id or actor_id or 'anonymous'}"
        return await cls.record_event(
            session=db_session,
            actor=effective_actor,
            action=action,
            resource_type=target_type,
            resource_id=target_id,
            project_id=project_id,
            after=details,
        )
