"""Production Remote MCP Authentication and OAuth 2.0 PKCE Provider for CortexForge.

Implements:
1. OAuth 2.0 PKCE (RFC 7636, RFC 6749, RFC 8414, RFC 9728) for Claude Custom Connectors
2. Dynamic Client Registration (RFC 7591)
3. Dual-mode Token Verification (OAuth Bearer Tokens + Direct CortexForge Agent/Session Keys)
4. Request-scoped Principal Isolation (Zero cross-user/cross-project context leakage)
"""

import contextvars
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    TokenVerifier,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlalchemy import select
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from cortexforge.core.db import session_scope
from cortexforge.core.models import (
    MCPOAuthAuthorizationCode,
    MCPOAuthClient,
    MCPOAuthToken,
    Project,
    ProjectMembership,
    User,
)
from cortexforge.security.auth import (
    Principal,
    resolve_principal_from_token,
)
from cortexforge.security.crypto import hash_token

logger = logging.getLogger(__name__)

# ContextVar storing the current HTTP request for OAuth authorization extraction
current_mcp_request: contextvars.ContextVar[Request | None] = contextvars.ContextVar(
    "current_mcp_request", default=None
)

DEFAULT_MCP_SCOPES = [
    "project:read",
    "context:read",
    "code:read",
    "memory:read",
    "memory:write",
    "graph:read",
    "scan:trigger",
]


class CortexForgeOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, AccessToken, RefreshToken]
):
    """Production OAuth 2.0 Authorization Server Provider for CortexForge MCP."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Load OAuth client information by client_id."""
        if not client_id:
            return None

        async with session_scope() as session:
            stmt = select(MCPOAuthClient).where(MCPOAuthClient.client_id == client_id)
            res = await session.execute(stmt)
            client_row = res.scalars().first()
            if not client_row:
                return None

            redirect_uris = [AnyUrl(u) for u in (client_row.redirect_uris or [])]
            scopes_str = " ".join(client_row.scopes) if client_row.scopes else " ".join(DEFAULT_MCP_SCOPES)
            return OAuthClientInformationFull(
                client_id=client_row.client_id,
                client_name=client_row.client_name,
                redirect_uris=redirect_uris,
                grant_types=client_row.grant_types or ["authorization_code"],
                response_types=client_row.response_types or ["code"],
                token_endpoint_auth_method=client_row.token_endpoint_auth_method or "none",
                scope=scopes_str,
            )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Register or update an OAuth client dynamically."""
        async with session_scope() as session:
            stmt = select(MCPOAuthClient).where(MCPOAuthClient.client_id == client_info.client_id)
            res = await session.execute(stmt)
            existing = res.scalars().first()

            redirect_uris = [str(u) for u in (client_info.redirect_uris or [])]
            grant_types = client_info.grant_types or ["authorization_code"]
            response_types = client_info.response_types or ["code"]
            auth_method = client_info.token_endpoint_auth_method or "none"
            scopes = client_info.scope.split(" ") if client_info.scope else DEFAULT_MCP_SCOPES
            secret_hash = (
                hash_token(client_info.client_secret)
                if client_info.client_secret
                else None
            )

            if existing:
                existing.client_name = client_info.client_name
                existing.redirect_uris = redirect_uris
                existing.grant_types = grant_types
                existing.response_types = response_types
                existing.token_endpoint_auth_method = auth_method
                existing.scopes = scopes
                if secret_hash:
                    existing.client_secret_hash = secret_hash
            else:
                client_row = MCPOAuthClient(
                    id=str(uuid.uuid4()),
                    client_id=client_info.client_id,
                    client_name=client_info.client_name,
                    client_secret_hash=secret_hash,
                    redirect_uris=redirect_uris,
                    grant_types=grant_types,
                    response_types=response_types,
                    token_endpoint_auth_method=auth_method,
                    scopes=scopes,
                )
                session.add(client_row)

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Issue an authorization code for an authenticated user and return redirect URI."""
        req = current_mcp_request.get()
        user: User | None = None

        async with session_scope() as session:
            if req:
                # 1. Check session cookie
                cookie_token = req.cookies.get("cortex_session") or req.cookies.get(
                    "cortexforge_session"
                )
                if cookie_token:
                    principal = await resolve_principal_from_token(session, cookie_token)
                    if principal and principal.user_id:
                        user = await session.get(User, principal.user_id)

                # 2. Check Authorization header if cookie was not present
                if not user:
                    auth_hdr = req.headers.get("Authorization")
                    if auth_hdr and auth_hdr.lower().startswith("bearer "):
                        tok = auth_hdr[7:].strip()
                        principal = await resolve_principal_from_token(session, tok)
                        if principal and principal.user_id:
                            user = await session.get(User, principal.user_id)

                # 3. Check query param session token (e.g. redirected from login page)
                if not user:
                    query_tok = req.query_params.get("session_token") or req.query_params.get(
                        "cortex_session"
                    )
                    if query_tok:
                        principal = await resolve_principal_from_token(session, query_tok)
                        if principal and principal.user_id:
                            user = await session.get(User, principal.user_id)

            if not user or user.status != "ACTIVE":
                raise AuthorizeError(
                    error="access_denied",
                    error_description="User authentication required. Please sign in to CortexForge.",
                )

            # Issue authorization code
            code = secrets.token_urlsafe(32)
            now = datetime.now(UTC)
            scopes = params.scopes or DEFAULT_MCP_SCOPES

            auth_code_row = MCPOAuthAuthorizationCode(
                id=str(uuid.uuid4()),
                code=code,
                client_id=client.client_id,
                user_id=user.id,
                redirect_uri=str(params.redirect_uri),
                code_challenge=params.code_challenge,
                code_challenge_method="S256",
                scope=" ".join(scopes),
                expires_at=now + timedelta(minutes=5),
            )
            session.add(auth_code_row)

        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        """Load and validate an authorization code."""
        if not authorization_code:
            return None

        async with session_scope() as session:
            stmt = select(MCPOAuthAuthorizationCode).where(
                MCPOAuthAuthorizationCode.code == authorization_code,
                MCPOAuthAuthorizationCode.client_id == client.client_id,
                MCPOAuthAuthorizationCode.used_at.is_(None),
            )
            res = await session.execute(stmt)
            code_row = res.scalars().first()
            if not code_row:
                return None

            now = datetime.now(UTC)
            exp = code_row.expires_at
            if exp and exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if exp and exp < now:
                return None

            return AuthorizationCode(
                code=code_row.code,
                scopes=code_row.scope.split() if code_row.scope else DEFAULT_MCP_SCOPES,
                expires_at=exp.timestamp() if exp else None,
                client_id=code_row.client_id,
                code_challenge=code_row.code_challenge,
                redirect_uri=AnyUrl(code_row.redirect_uri),
                redirect_uri_provided_explicitly=True,
                subject=code_row.user_id,
            )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Exchange authorization code for an access token."""
        now = datetime.now(UTC)
        token_str = "cf_mcp_" + secrets.token_urlsafe(32)
        token_h = hash_token(token_str)
        expires_at = now + timedelta(days=30)
        scopes = authorization_code.scopes or DEFAULT_MCP_SCOPES

        async with session_scope() as session:
            stmt = select(MCPOAuthAuthorizationCode).where(
                MCPOAuthAuthorizationCode.code == authorization_code.code,
                MCPOAuthAuthorizationCode.used_at.is_(None),
            )
            res = await session.execute(stmt)
            code_row = res.scalars().first()
            if not code_row:
                raise TokenError(
                    error="invalid_grant",
                    error_description="Authorization code invalid or already used",
                )

            # Mark code used
            code_row.used_at = now

            # Create token record
            token_row = MCPOAuthToken(
                id=str(uuid.uuid4()),
                token_hash=token_h,
                client_id=client.client_id,
                user_id=authorization_code.subject or code_row.user_id,
                token_type="Bearer",
                scope=" ".join(scopes),
                expires_at=expires_at,
            )
            session.add(token_row)

        return OAuthToken(
            access_token=token_str,
            token_type="Bearer",
            expires_in=30 * 86400,
            scope=" ".join(scopes),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Load and verify an access token (OAuth token or direct Agent/Session token)."""
        if not token or not token.strip():
            return None

        clean_token = token.strip()
        if clean_token.lower().startswith("bearer "):
            clean_token = clean_token[7:].strip()
        if not clean_token:
            return None

        token_h = hash_token(clean_token)
        now = datetime.now(UTC)

        async with session_scope() as session:
            # 1. Check MCPOAuthToken table
            stmt = select(MCPOAuthToken).where(
                MCPOAuthToken.token_hash == token_h,
                MCPOAuthToken.revoked_at.is_(None),
                MCPOAuthToken.expires_at > now,
            )
            res = await session.execute(stmt)
            mcp_token = res.scalars().first()
            if mcp_token:
                user = await session.get(User, mcp_token.user_id)
                if not user or user.status != "ACTIVE":
                    return None

                proj_res = await session.execute(
                    select(Project.id).where(Project.owner_user_id == user.id)
                )
                owned_ids = set(proj_res.scalars().all())
                mem_res = await session.execute(
                    select(ProjectMembership.project_id).where(
                        ProjectMembership.user_id == user.id
                    )
                )
                owned_ids.update(mem_res.scalars().all())

                is_admin = bool(getattr(user, "is_admin", False))
                scopes = (
                    mcp_token.scope.split()
                    if mcp_token.scope
                    else DEFAULT_MCP_SCOPES
                )

                tok_exp = mcp_token.expires_at
                if tok_exp and tok_exp.tzinfo is None:
                    tok_exp = tok_exp.replace(tzinfo=UTC)
                expires_at_val = int(tok_exp.timestamp()) if tok_exp else None

                return AccessToken(
                    token=clean_token,
                    client_id=mcp_token.client_id,
                    scopes=scopes,
                    expires_at=expires_at_val,
                    subject=user.id,
                    claims={
                        "principal_id": user.id,
                        "actor_type": "USER",
                        "user_id": user.id,
                        "email": user.email,
                        "role": "admin" if is_admin else "user",
                        "is_admin": is_admin,
                        "allowed_project_ids": list(owned_ids),
                    },
                )

            # 2. Dual Authentication: Direct Agent Credential or User Session
            principal: Principal | None = await resolve_principal_from_token(
                session, clean_token
            )
            if principal:
                return AccessToken(
                    token=clean_token,
                    client_id="cortexforge",
                    scopes=DEFAULT_MCP_SCOPES,
                    expires_at=None,
                    subject=principal.principal_id,
                    claims={
                        "principal_id": principal.principal_id,
                        "actor_type": principal.actor_type,
                        "user_id": principal.user_id,
                        "agent_id": principal.agent_id,
                        "role": principal.role,
                        "is_admin": principal.is_admin,
                        "allowed_project_ids": list(principal.allowed_project_ids),
                    },
                )

        return None

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        """Refresh tokens are not used."""
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Refresh tokens not supported."""
        raise TokenError(
            error="unsupported_grant_type",
            error_description="Refresh tokens are not supported",
        )

    async def revoke_token(self, token: AccessToken | RefreshToken | str) -> None:
        """Revoke an active access token."""
        raw_token = token if isinstance(token, str) else getattr(token, "token", None)
        if raw_token:
            token_h = hash_token(raw_token)
            now = datetime.now(UTC)
            async with session_scope() as session:
                stmt = select(MCPOAuthToken).where(MCPOAuthToken.token_hash == token_h)
                res = await session.execute(stmt)
                mcp_token = res.scalars().first()
                if mcp_token:
                    mcp_token.revoked_at = now


class CortexForgeTokenVerifier(TokenVerifier):
    """TokenVerifier implementation wrapping CortexForgeOAuthProvider."""

    def __init__(self, provider: CortexForgeOAuthProvider):
        self.provider = provider

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.provider.load_access_token(token)


class CortexForgeMCPContextMiddleware:
    """Middleware that binds current_mcp_request and synchronizes _CURRENT_MCP_CALLER per request.

    Guarantees:
    - Zero cross-request ContextVar leakage (always resets in finally block)
    - Correct principal attribution from Bearer token
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        from cortexforge.apps.mcp.server import _CURRENT_MCP_CALLER

        req_reset_token = None
        if scope["type"] == "http":
            req = Request(scope, receive=receive)
            req_reset_token = current_mcp_request.set(req)

        caller_reset_token = None
        user = scope.get("user")
        if isinstance(user, AuthenticatedUser) and user.access_token:
            acc_tok = user.access_token
            claims: dict[str, Any] = acc_tok.claims or {}
            caller_reset_token = _CURRENT_MCP_CALLER.set(
                {
                    "token": acc_tok.token,
                    "principal_id": acc_tok.subject,
                    "claims": claims,
                    "user_id": claims.get("user_id"),
                    "agent_id": claims.get("agent_id"),
                    "role": claims.get("role"),
                    "is_admin": claims.get("is_admin", False),
                }
            )

        try:
            await self.app(scope, receive, send)
        finally:
            if caller_reset_token is not None:
                _CURRENT_MCP_CALLER.reset(caller_reset_token)
            if req_reset_token is not None:
                current_mcp_request.reset(req_reset_token)
