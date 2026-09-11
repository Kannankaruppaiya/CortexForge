"""REST API routes for AI Agents management and explicit Project Permissions.

Architecture:
- AI Agents are separate principals from human Users.
- An Agent is created and owned by a verified User.
- An Agent only gains access to a Project through explicit AgentProjectPermission grants.
- Never assume agent.owner_user_id == user allows the agent to access all projects.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.db import get_db_session
from cortexforge.core.models import (
    Agent,
    AgentCredential,
    AgentProjectPermission,
    Project,
)
from cortexforge.core.schemas import (
    AgentCreate,
    AgentCreatedResponse,
    AgentCredentialCreate,
    AgentCredentialRead,
    AgentCredentialSecretResponse,
    AgentPermissionGrant,
    AgentPermissionRead,
    AgentRead,
)
from cortexforge.security.audit import AuditService
from cortexforge.security.auth import Principal, get_current_principal
from cortexforge.security.crypto import generate_agent_key

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentRead])
async def list_agents(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[AgentRead]:
    """List all AI agents registered by the current user."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )

    res = await session.execute(
        select(Agent)
        .where(Agent.owner_user_id == principal.user_id)
        .order_by(Agent.created_at.desc())
    )
    return [AgentRead.model_validate(a) for a in res.scalars().all()]


@router.post(
    "", response_model=AgentCreatedResponse, status_code=status.HTTP_201_CREATED
)
async def create_agent(
    payload: AgentCreate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> AgentCreatedResponse:
    """Create a new AI Agent principal with a unique API key."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )

    raw_api_key, api_key_h = generate_agent_key()
    import uuid

    key_id = f"ca_key_{uuid.uuid4().hex[:16]}"

    agent = Agent(
        owner_user_id=principal.user_id,
        name=payload.name.strip(),
        type=payload.type.strip(),
        status="ACTIVE",
        api_key_hash=api_key_h,
    )
    session.add(agent)
    await session.flush()

    # Create initial AgentCredential record for rotation (§6)
    cred = AgentCredential(
        agent_id=agent.id,
        key_id=key_id,
        key_hash=api_key_h,
        name="initial",
        created_at=datetime.now(UTC),
    )
    session.add(cred)
    await session.commit()
    await session.refresh(agent)

    await AuditService.record(
        db_session=session,
        action="AGENT_CREATE",
        target_type="agent",
        target_id=agent.id,
        user_id=principal.user_id,
        actor_type="USER",
        details={"name": agent.name, "type": agent.type, "key_id": key_id},
    )

    return AgentCreatedResponse(
        agent=AgentRead.model_validate(agent),
        api_key=raw_api_key,
    )


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> AgentRead:
    """Retrieve details for a specific AI agent."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )

    agent = await session.get(Agent, agent_id)
    if not agent or agent.owner_user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found."
        )

    return AgentRead.model_validate(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete an AI agent and revoke its permissions."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )

    agent = await session.get(Agent, agent_id)
    if not agent or agent.owner_user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found."
        )

    await session.delete(agent)
    await session.commit()

    await AuditService.record(
        db_session=session,
        action="AGENT_DELETE",
        target_type="agent",
        target_id=agent_id,
        user_id=principal.user_id,
        actor_type="USER",
    )


@router.get("/{agent_id}/permissions", response_model=list[AgentPermissionRead])
async def list_agent_permissions(
    agent_id: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[AgentPermissionRead]:
    """List active project permissions for an AI agent."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )

    agent = await session.get(Agent, agent_id)
    if not agent or agent.owner_user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found."
        )

    now = datetime.now(UTC)
    res = await session.execute(
        select(AgentProjectPermission).where(
            AgentProjectPermission.agent_id == agent_id,
            AgentProjectPermission.revoked_at.is_(None),
            (AgentProjectPermission.expires_at.is_(None))
            | (AgentProjectPermission.expires_at > now),
        )
    )
    return [AgentPermissionRead.model_validate(p) for p in res.scalars().all()]


@router.post(
    "/{agent_id}/permissions",
    response_model=AgentPermissionRead,
    status_code=status.HTTP_201_CREATED,
)
async def grant_agent_permission(
    agent_id: str,
    payload: AgentPermissionGrant,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> AgentPermissionRead:
    """Explicitly authorize an AI agent to access a specific project."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )

    agent = await session.get(Agent, agent_id)
    if not agent or agent.owner_user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found."
        )

    # Verify that the project is owned by the current user
    project = await session.get(Project, payload.project_id)
    if not project or project.owner_user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot grant agent access to a project you do not own.",
        )

    now = datetime.now(UTC)
    expires_at = (
        now + timedelta(seconds=payload.expires_in_seconds)
        if payload.expires_in_seconds
        else None
    )

    # Check if existing permission exists
    res = await session.execute(
        select(AgentProjectPermission).where(
            AgentProjectPermission.agent_id == agent_id,
            AgentProjectPermission.project_id == payload.project_id,
        )
    )
    existing = res.scalars().first()

    if existing:
        existing.revoked_at = None
        existing.scopes = payload.scopes
        existing.expires_at = expires_at
        perm = existing
    else:
        perm = AgentProjectPermission(
            agent_id=agent_id,
            project_id=payload.project_id,
            scopes=payload.scopes,
            expires_at=expires_at,
            created_at=now,
        )
        session.add(perm)

    await session.commit()
    await session.refresh(perm)

    await AuditService.record(
        db_session=session,
        action="AGENT_PERMISSION_GRANT",
        target_type="agent_permission",
        target_id=perm.id,
        user_id=principal.user_id,
        actor_type="USER",
        project_id=payload.project_id,
        details={"agent_id": agent_id, "scopes": payload.scopes},
    )

    return AgentPermissionRead.model_validate(perm)


@router.delete(
    "/{agent_id}/permissions/{project_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_agent_permission(
    agent_id: str,
    project_id: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Revoke an AI agent's authorization to access a project."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )

    agent = await session.get(Agent, agent_id)
    if not agent or agent.owner_user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found."
        )

    res = await session.execute(
        select(AgentProjectPermission).where(
            AgentProjectPermission.agent_id == agent_id,
            AgentProjectPermission.project_id == project_id,
            AgentProjectPermission.revoked_at.is_(None),
        )
    )
    perm = res.scalars().first()
    if perm:
        perm.revoked_at = datetime.now(UTC)
        await session.commit()

        await AuditService.record(
            db_session=session,
            action="AGENT_PERMISSION_REVOKE",
            target_type="agent_permission",
            target_id=perm.id,
            user_id=principal.user_id,
            actor_type="USER",
            project_id=project_id,
            details={"agent_id": agent_id},
        )


@router.get("/{agent_id}/credentials", response_model=list[AgentCredentialRead])
async def list_agent_credentials(
    agent_id: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[AgentCredentialRead]:
    """List credentials for an AI agent (§6)."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    agent = await session.get(Agent, agent_id)
    if not agent or agent.owner_user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found."
        )
    res = await session.execute(
        select(AgentCredential)
        .where(AgentCredential.agent_id == agent_id)
        .order_by(AgentCredential.created_at.desc())
    )
    return [AgentCredentialRead.model_validate(c) for c in res.scalars().all()]


@router.post(
    "/{agent_id}/credentials",
    response_model=AgentCredentialSecretResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_credential(
    agent_id: str,
    payload: AgentCredentialCreate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> AgentCredentialSecretResponse:
    """Generate a new rotated credential for an AI agent. Secret is returned exactly once (§6)."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    agent = await session.get(Agent, agent_id)
    if not agent or agent.owner_user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found."
        )
    import uuid

    raw_api_key, api_key_h = generate_agent_key()
    key_id = f"ca_key_{uuid.uuid4().hex[:16]}"
    now = datetime.now(UTC)
    expires_at = (
        now + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days
        else None
    )

    cred = AgentCredential(
        agent_id=agent_id,
        key_id=key_id,
        key_hash=api_key_h,
        name=payload.name.strip(),
        created_at=now,
        expires_at=expires_at,
    )
    session.add(cred)
    await session.commit()
    await session.refresh(cred)

    await AuditService.record(
        db_session=session,
        action="AGENT_CREDENTIAL_CREATE",
        target_type="agent_credential",
        target_id=cred.id,
        user_id=principal.user_id,
        actor_type="USER",
        details={"agent_id": agent_id, "key_id": key_id},
    )

    read_obj = AgentCredentialRead.model_validate(cred)
    return AgentCredentialSecretResponse(
        **read_obj.model_dump(),
        raw_api_key=raw_api_key,
    )


@router.delete(
    "/{agent_id}/credentials/{key_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_agent_credential(
    agent_id: str,
    key_id: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Revoke a specific credential for an AI agent (§6)."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    agent = await session.get(Agent, agent_id)
    if not agent or agent.owner_user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found."
        )
    res = await session.execute(
        select(AgentCredential).where(
            AgentCredential.agent_id == agent_id,
            AgentCredential.key_id == key_id,
        )
    )
    cred = res.scalars().first()
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found."
        )
    cred.revoked_at = datetime.now(UTC)
    await session.commit()

    await AuditService.record(
        db_session=session,
        action="AGENT_CREDENTIAL_REVOKE",
        target_type="agent_credential",
        target_id=cred.id,
        user_id=principal.user_id,
        actor_type="USER",
        details={"agent_id": agent_id, "key_id": key_id},
    )
