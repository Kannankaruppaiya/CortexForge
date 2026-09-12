"""Audit trail for cognitively significant actions (specification section 44).

``MemoryVersion`` records what happened to one memory. This records what happened
to the *system*: who approved a lesson, who changed an architecture rule, who
switched a provider, who reconfigured a project. Those actions change what
CortexForge will believe and how it will verify things, and none of them left a
trace before.

Two disciplines apply:

* **Never log secrets.** Every payload passes through the redactor before it is
  stored, because "before" and "after" snapshots of a configuration change are
  exactly where credentials leak into a log (section 41).
* **An audit entry is not a substitute for the action.** Writing the entry is part
  of the same transaction as the change it describes, so the two cannot diverge.
"""

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import AuditLog
from cortexforge.security.redactor import SecretRedactor

logger = logging.getLogger(__name__)


class AuditAction:
    """The action names this system records. Kept as constants so that queries
    over the audit log can be written against a known vocabulary."""

    MEMORY_CREATED = "MEMORY_CREATED"
    MEMORY_REVISED = "MEMORY_REVISED"
    MEMORY_INVALIDATED = "MEMORY_INVALIDATED"
    MEMORY_APPROVED = "MEMORY_APPROVED"
    MEMORY_REJECTED = "MEMORY_REJECTED"
    CLAIM_VERIFIED = "CLAIM_VERIFIED"
    ARCHITECTURE_RULE_CHANGED = "ARCHITECTURE_RULE_CHANGED"
    PROVIDER_CHANGED = "PROVIDER_CHANGED"
    PROJECT_CONFIGURED = "PROJECT_CONFIGURED"
    WEBHOOK_PROCESSED = "WEBHOOK_PROCESSED"
    CONSOLIDATION_RUN = "CONSOLIDATION_RUN"
    RECONCILIATION_RUN = "RECONCILIATION_RUN"


def _redact(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip secrets from an audit payload before it is persisted."""
    if payload is None:
        return None
    try:
        serialized = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return {"unserializable": str(type(payload))}
    return json.loads(SecretRedactor.redact_secrets(serialized))


async def record_audit(
    session: AsyncSession,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    actor: str = "system",
    project_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str = "",
    trace_id: str | None = None,
) -> AuditLog:
    """Append an audit entry. Caller commits, so the entry shares the change's fate."""
    entry = AuditLog(
        project_id=project_id,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before=_redact(before),
        after=_redact(after),
        reason=reason,
        trace_id=trace_id,
    )
    session.add(entry)
    return entry


async def read_audit(
    session: AsyncSession,
    project_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    limit: int = 100,
) -> list[AuditLog]:
    """Read the audit trail, newest first."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if project_id:
        stmt = stmt.where(AuditLog.project_id == project_id)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())
