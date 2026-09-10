"""Server-side human authorization boundary (Specification section 29).

Enforces that human confirmation cannot be forged by an untrusted agent.
Approval records are cryptographically keyed, bounded by project and payload digest,
time-limited, and single-use.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import HumanApprovalRecord


class ApprovalService:
    """Manages human authorization workflows for sensitive memory mutations."""

    @staticmethod
    def compute_payload_digest(title: str, content: str, memory_type: str) -> str:
        """Compute deterministic sha256 digest of memory payload."""
        data = f"{title.strip()}|{content.strip()}|{memory_type.strip().upper()}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @classmethod
    async def create_approval_request(
        cls,
        session: AsyncSession,
        project_id: str,
        title: str,
        content: str,
        memory_type: str,
        operation: str = "memory_create",
        requested_by: str = "agent",
    ) -> HumanApprovalRecord:
        """Create a pending authorization request."""
        digest = cls.compute_payload_digest(title, content, memory_type)
        token = secrets.token_urlsafe(32)
        record = HumanApprovalRecord(
            project_id=project_id,
            operation=operation,
            payload_digest=digest,
            requested_by=requested_by,
            token=token,
            status="PENDING",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record

    @classmethod
    async def approve_request(
        cls,
        session: AsyncSession,
        request_id: str | None = None,
        token: str | None = None,
        approved_by: str = "user:admin",
        ttl_seconds: int = 3600,
    ) -> HumanApprovalRecord:
        """Approve a pending request and return the approved record."""
        if request_id:
            record = await session.get(HumanApprovalRecord, request_id)
        elif token:
            stmt = select(HumanApprovalRecord).where(HumanApprovalRecord.token == token)
            record = (await session.execute(stmt)).scalars().first()
        else:
            raise ValueError("Either request_id or token must be provided.")

        if not record:
            raise ValueError("Approval request does not exist.")
        if record.status != "PENDING":
            raise ValueError(f"Request is already {record.status}.")

        record.status = "APPROVED"
        record.approved_by = approved_by
        record.expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        await session.commit()
        await session.refresh(record)
        return record

    @classmethod
    async def validate_and_consume_token(
        cls,
        session: AsyncSession,
        token: str,
        project_id: str,
        title: str,
        content: str,
        memory_type: str,
    ) -> tuple[HumanApprovalRecord | None, str | None]:
        """Validate an approval token against project, payload digest, and expiry."""
        if not token:
            return None, "Empty token provided."

        stmt = select(HumanApprovalRecord).where(
            HumanApprovalRecord.token == token,
            HumanApprovalRecord.project_id == project_id,
        )
        record = (await session.execute(stmt)).scalars().first()
        if not record:
            return None, "No matching approval record found."

        if record.status != "APPROVED":
            return (
                None,
                f"Approval record status is '{record.status}', required 'APPROVED'.",
            )

        # Verify expiration
        now = datetime.now(UTC)
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if now > expires_at:
            record.status = "EXPIRED"
            await session.commit()
            return None, "Approval token has expired."

        # Verify payload digest matches exactly
        expected_digest = cls.compute_payload_digest(title, content, memory_type)
        if record.payload_digest != expected_digest:
            return None, "Payload digest does not match approved memory content."

        # Mark single-use token consumed atomically via CAS to prevent concurrent replay
        cas_stmt = (
            update(HumanApprovalRecord)
            .where(
                HumanApprovalRecord.id == record.id,
                HumanApprovalRecord.status == "APPROVED",
            )
            .values(status="CONSUMED", consumed_at=now)
        )
        res = await session.execute(cas_stmt)
        if res.rowcount == 0:
            return None, "Approval token was concurrently consumed."

        await session.commit()
        await session.refresh(record)
        return record, None
