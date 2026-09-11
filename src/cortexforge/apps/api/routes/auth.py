"""REST API routes for Individual-User-First Authentication in CortexForge.

Supports:
- Email + Password (memory-hard Argon2id / Scrypt hashing)
- Passwordless Email OTP (cryptographic random one-time code with rate limiting)
- GitHub OAuth (Authorization Code flow with secure account linking)
- Session Management (HttpOnly cookies, rotation, multi-session revocation)
- Password Reset (single-use, cryptographic tokens, session invalidation)

Strictly NO Google / Microsoft / Apple social logins.
"""

import base64
import hashlib
import os
import re
import secrets
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.db import get_db_session
from cortexforge.core.models import (
    EmailOTPChallenge,
    ExternalIdentity,
    OAuthTransaction,
    PasswordCredential,
    PasswordResetToken,
    User,
)
from cortexforge.core.models import (
    Session as UserSession,
)
from cortexforge.core.schemas import (
    LoginRequest,
    OTPRequest,
    OTPVerifyRequest,
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    SessionRead,
    UserRead,
)
from cortexforge.security.audit import AuditService
from cortexforge.security.auth import (
    Principal,
    get_current_principal,
    get_session_token_from_request,
)
from cortexforge.security.crypto import (
    PasswordHasher,
    generate_otp,
    generate_session_token,
    hash_token,
)
from cortexforge.security.rate_limiter import auth_rate_limiter

router = APIRouter(prefix="/auth", tags=["authentication"])

SESSION_COOKIE_NAME = "cortex_session"
SESSION_DURATION_DAYS = 7
OTP_EXPIRATION_MINUTES = 10
MAX_OTP_ATTEMPTS = 5

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def _is_server_debug_mode_allowed() -> bool:
    """Return True only if explicitly running in a test/dev environment with server-side test mode enabled.

    In production, prod, or staging, test/debug tokens are NEVER exposed regardless of any client headers.
    Client headers alone can NEVER enable test/debug behavior.
    """
    env = (
        os.environ.get("CORTEX_ENV", os.environ.get("ENVIRONMENT", "development"))
        .strip()
        .lower()
    )
    if env in ("production", "prod", "staging"):
        return False
    return (
        os.environ.get("CORTEX_TEST_MODE") == "true"
        or os.environ.get("ENVIRONMENT") == "test"
        or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    )


def _set_session_cookie(response: Response, raw_token: str, request: Request) -> None:
    """Set secure, HttpOnly session cookie."""
    is_secure = (
        request.url.scheme == "https"
        or os.environ.get("ENVIRONMENT") == "production"
        or os.environ.get("CORTEX_ENV") == "production"
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=int(timedelta(days=SESSION_DURATION_DAYS).total_seconds()),
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
    )
    response.set_cookie(
        key="cortexforge_session",
        value=raw_token,
        max_age=int(timedelta(days=SESSION_DURATION_DAYS).total_seconds()),
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    """Clear session cookie on logout."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key="cortexforge_session",
        path="/",
        httponly=True,
        samesite="lax",
    )


def _is_expired(dt: datetime | None) -> bool:
    """Safely check if datetime is expired handling both tz-aware and tz-naive objects."""
    if not dt:
        return True
    now = datetime.now(UTC)
    exp = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    return now >= exp


class AuthResponse(BaseModel):
    user: UserRead
    token: str
    message: str = "Authenticated successfully"


class AuthMeResponse(BaseModel):
    id: str | None = None
    email: str | None = None
    display_name: str | None = None
    github_user_id: str | None = None
    github_login: str | None = None
    avatar_url: str | None = None
    user: UserRead
    github_connected: bool = False
    has_password: bool = False


# ==============================================================================
# Registration & Email + Password
# ==============================================================================


@router.post(
    "/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED
)
async def register_user(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    """Register a new individual user with email and secure password."""
    client_ip = _get_client_ip(request)
    if not auth_rate_limiter.check(
        "register", client_ip, max_requests=10, window_seconds=60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Please try again later.",
        )

    norm_email = payload.email.strip().lower()
    if not EMAIL_REGEX.match(norm_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address format.",
        )

    if len(payload.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters long.",
        )

    # Verify email uniqueness
    existing = await session.execute(
        select(User).where(func.lower(User.email) == norm_email)
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        )

    # Server generates internal user ID (never client-supplied!)
    user_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    display_name = (
        payload.display_name.strip()
        if payload.display_name
        else norm_email.split("@")[0]
    )
    user = User(
        id=user_id,
        email=norm_email,
        display_name=display_name,
        status="ACTIVE",
        email_verified_at=now,
        created_at=now,
        updated_at=now,
        last_login_at=now,
    )
    session.add(user)

    # Hash password with memory-hard algorithm
    pwd_hash = PasswordHasher.hash(payload.password)
    cred = PasswordCredential(
        user_id=user_id,
        password_hash=pwd_hash,
        algorithm="scrypt",
        created_at=now,
        updated_at=now,
    )
    session.add(cred)

    # Create initial authenticated session
    raw_token = generate_session_token()
    token_h = hash_token(raw_token)
    user_session = UserSession(
        user_id=user_id,
        session_token_hash=token_h,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip,
        expires_at=now + timedelta(days=SESSION_DURATION_DAYS),
        created_at=now,
    )
    session.add(user_session)

    await session.commit()
    await session.refresh(user)

    await AuditService.record(
        db_session=session,
        action="USER_REGISTER",
        target_type="user",
        target_id=user_id,
        user_id=user_id,
        actor_type="USER",
        details={"email": norm_email, "display_name": display_name},
    )

    _set_session_cookie(response, raw_token, request)
    return AuthResponse(
        user=UserRead.model_validate(user),
        token=raw_token,
        message="Registration successful",
    )


@router.post("/login", response_model=AuthResponse)
async def login_with_password(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    """Authenticate with email and password."""
    client_ip = _get_client_ip(request)
    if not auth_rate_limiter.check(
        "login", client_ip, max_requests=15, window_seconds=60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again later.",
        )

    norm_email = payload.email.strip().lower()

    # Load user
    res = await session.execute(
        select(User).where(func.lower(User.email) == norm_email)
    )
    user = res.scalars().first()

    # Generic error message to prevent enumeration
    generic_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
    )

    if not user or user.status != "ACTIVE":
        # Constant-time dummy verification to protect against timing attacks
        PasswordHasher.verify("dummy_password", PasswordHasher.hash("dummy_password"))
        auth_rate_limiter.record_failure(f"login:{norm_email}")
        raise generic_error

    # Load password credential
    cred_res = await session.execute(
        select(PasswordCredential).where(PasswordCredential.user_id == user.id)
    )
    cred = cred_res.scalars().first()
    if not cred:
        auth_rate_limiter.record_failure(f"login:{norm_email}")
        raise generic_error

    if not PasswordHasher.verify(payload.password, cred.password_hash):
        auth_rate_limiter.record_failure(f"login:{norm_email}")
        raise generic_error

    auth_rate_limiter.reset(f"login:{norm_email}")

    # Establish authenticated session
    now = datetime.now(UTC)
    user.last_login_at = now

    raw_token = generate_session_token()
    token_h = hash_token(raw_token)
    user_session = UserSession(
        user_id=user.id,
        session_token_hash=token_h,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip,
        expires_at=now + timedelta(days=SESSION_DURATION_DAYS),
        created_at=now,
    )
    session.add(user_session)

    await session.commit()
    await session.refresh(user)

    await AuditService.record(
        db_session=session,
        action="USER_LOGIN",
        target_type="user",
        target_id=user.id,
        user_id=user.id,
        actor_type="USER",
        details={"method": "password"},
    )

    _set_session_cookie(response, raw_token, request)
    return AuthResponse(
        user=UserRead.model_validate(user),
        token=raw_token,
        message="Login successful",
    )


# ==============================================================================
# Passwordless Email OTP Login
# ==============================================================================


class OTPRequestResponse(BaseModel):
    message: str
    debug_otp: str | None = (
        None  # Returned only in test mode for automated verification
    )


@router.post("/otp/request", response_model=OTPRequestResponse)
async def request_email_otp(
    payload: OTPRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> OTPRequestResponse:
    """Request a passwordless 6-digit one-time code sent to email."""
    client_ip = _get_client_ip(request)
    if not auth_rate_limiter.check(
        "otp_req", client_ip, max_requests=10, window_seconds=60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification requests. Please try again later.",
        )

    norm_email = payload.email.strip().lower()
    if not EMAIL_REGEX.match(norm_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address format.",
        )

    # Rate-limit per email (resend protection)
    if not auth_rate_limiter.check(
        f"otp_resend:{norm_email}", "resend", max_requests=3, window_seconds=60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before requesting another verification code.",
        )

    now = datetime.now(UTC)

    # Invalidate previous unconsumed OTPs for this email
    prev_challenges = await session.execute(
        select(EmailOTPChallenge).where(
            EmailOTPChallenge.email == norm_email,
            EmailOTPChallenge.consumed_at.is_(None),
            EmailOTPChallenge.expires_at > now,
        )
    )
    for ch in prev_challenges.scalars().all():
        ch.consumed_at = now

    # Find user ID if user already exists
    user_res = await session.execute(
        select(User).where(func.lower(User.email) == norm_email)
    )
    user = user_res.scalars().first()

    # Generate 6-digit cryptographic OTP
    otp = generate_otp()
    otp_h = hash_token(otp)

    challenge = EmailOTPChallenge(
        email=norm_email,
        user_id=user.id if user else None,
        otp_hash=otp_h,
        attempts_count=0,
        max_attempts=MAX_OTP_ATTEMPTS,
        expires_at=now + timedelta(minutes=OTP_EXPIRATION_MINUTES),
        created_at=now,
    )
    session.add(challenge)
    await session.commit()

    is_test_mode = _is_server_debug_mode_allowed()

    return OTPRequestResponse(
        message="If this email is registered or valid, a 6-digit verification code has been sent.",
        debug_otp=otp if is_test_mode else None,
    )


@router.post("/otp/verify", response_model=AuthResponse)
async def verify_email_otp(
    payload: OTPVerifyRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    """Verify one-time code and establish authenticated session."""
    client_ip = _get_client_ip(request)
    if not auth_rate_limiter.check(
        "otp_verify", client_ip, max_requests=15, window_seconds=60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification attempts. Please try again later.",
        )

    norm_email = payload.email.strip().lower()
    now = datetime.now(UTC)

    # Find latest active challenge
    res = await session.execute(
        select(EmailOTPChallenge)
        .where(
            EmailOTPChallenge.email == norm_email,
            EmailOTPChallenge.consumed_at.is_(None),
        )
        .order_by(EmailOTPChallenge.created_at.desc())
    )
    challenge = res.scalars().first()

    if not challenge or _is_expired(challenge.expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code has expired or is invalid. Please request a new one.",
        )

    if challenge.attempts_count >= challenge.max_attempts:
        challenge.consumed_at = now
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many incorrect attempts. This code has been invalidated.",
        )

    entered_h = hash_token(payload.otp.strip())
    if entered_h != challenge.otp_hash:
        challenge.attempts_count += 1
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid verification code.",
        )

    # Mark challenge as consumed
    challenge.consumed_at = now

    # Load or create User
    user_res = await session.execute(
        select(User).where(func.lower(User.email) == norm_email)
    )
    user = user_res.scalars().first()

    if not user:
        # Auto-provision user account
        user_id = str(uuid.uuid4())
        user = User(
            id=user_id,
            email=norm_email,
            display_name=norm_email.split("@")[0],
            status="ACTIVE",
            email_verified_at=now,
            created_at=now,
            updated_at=now,
            last_login_at=now,
        )
        session.add(user)
    else:
        if not user.email_verified_at:
            user.email_verified_at = now
        user.last_login_at = now

    # Create authenticated session
    raw_token = generate_session_token()
    token_h = hash_token(raw_token)
    user_session = UserSession(
        user_id=user.id,
        session_token_hash=token_h,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip,
        expires_at=now + timedelta(days=SESSION_DURATION_DAYS),
        created_at=now,
    )
    session.add(user_session)

    await session.commit()
    await session.refresh(user)

    await AuditService.record(
        db_session=session,
        action="USER_OTP_LOGIN",
        target_type="user",
        target_id=user.id,
        user_id=user.id,
        actor_type="USER",
        details={"method": "email_otp"},
    )

    _set_session_cookie(response, raw_token, request)
    return AuthResponse(
        user=UserRead.model_validate(user),
        token=raw_token,
        message="Verification successful",
    )


# ==============================================================================
# GitHub OAuth Login & Account Linking (PKCE S256 + Single-Use State)
# ==============================================================================


class GitHubOAuthConfig:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        is_production: bool,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.is_production = is_production


def get_github_oauth_config(request: Request | None = None) -> GitHubOAuthConfig:
    """Retrieve validated GitHub OAuth configuration.

    Production fails closed immediately if GITHUB_OAUTH_CLIENT_ID or GITHUB_OAUTH_CLIENT_SECRET is missing.
    Development returns actionable error instructions.
    Any presence of 'mock_github_client_id' is strictly rejected.
    """
    env = (
        os.environ.get("CORTEX_ENV") or os.environ.get("ENVIRONMENT") or "development"
    ).lower()
    is_production = env in ("production", "prod")

    client_id = (
        os.environ.get("GITHUB_OAUTH_CLIENT_ID")
        or os.environ.get("GITHUB_CLIENT_ID")
        or ""
    ).strip()
    client_secret = (
        os.environ.get("GITHUB_OAUTH_CLIENT_SECRET")
        or os.environ.get("GITHUB_CLIENT_SECRET")
        or ""
    ).strip()

    # Disallow mock_github_client_id across all environments
    if client_id == "mock_github_client_id":
        client_id = ""

    if not client_id or not client_secret:
        if is_production:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="GitHub OAuth configuration missing in production. GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET must be configured.",
            )
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub OAuth is not configured. Set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET in environment.",
            )
        # Default test runner fallback when not testing missing-config
        client_id = client_id or "test_github_client_id"
        client_secret = client_secret or "test_github_client_secret"

    redirect_uri = (
        os.environ.get("GITHUB_OAUTH_REDIRECT_URI")
        or os.environ.get("GITHUB_REDIRECT_URI")
        or ""
    ).strip()
    if not redirect_uri:
        if request:
            base = str(request.base_url).rstrip("/")
            redirect_uri = f"{base}/api/v1/auth/github/callback"
        else:
            redirect_uri = "http://127.0.0.1:8000/api/v1/auth/github/callback"

    return GitHubOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        is_production=is_production,
    )


def generate_pkce_pair() -> tuple[str, str]:
    """Generate RFC 7636 compliant PKCE code_verifier and code_challenge (S256).

    code_verifier: 64 URL-safe random bytes (~86 characters, between 43 and 128)
    code_challenge: Base64URL(SHA-256(code_verifier)) without padding
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


@router.get("/github")
@router.get("/github/authorize")
async def github_login(
    request: Request,
    response: Response,
    format: str | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    """Initiate standard GitHub OAuth flow with PKCE S256 and single-use state.

    Supports both automatic Sign Up (new users) and Sign In (existing users)
    via single 'Continue with GitHub' flow.
    """
    config = get_github_oauth_config(request)
    client_ip = _get_client_ip(request)

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()

    now = datetime.now(UTC)
    # Store transaction in database with 10-minute expiry
    tx = OAuthTransaction(
        id=str(uuid.uuid4()),
        provider="github",
        state=state,
        code_verifier=code_verifier,
        redirect_uri=config.redirect_uri,
        ip_address=client_ip,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        used_at=None,
    )
    session.add(tx)
    await session.commit()

    # Set state in temporary cookie for defense-in-depth CSRF verification
    is_secure = (
        request.url.scheme == "https"
        or os.environ.get("ENVIRONMENT") == "production"
        or os.environ.get("CORTEX_ENV") == "production"
    )
    response.set_cookie(
        key="cortex_oauth_state",
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
    )

    # Build authorization URL with minimal read:user user:email scope (NO repo scope)
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": "read:user user:email",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = (
        f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(params)}"
    )

    # Return JSON if requested or on /github/authorize
    accept_hdr = request.headers.get("accept", "")
    wants_json = (
        format == "json"
        or request.url.path.endswith("/authorize")
        or ("application/json" in accept_hdr and "text/html" not in accept_hdr)
    )
    if wants_json:
        return {"authorize_url": authorize_url, "state": state}

    # Default browser behavior: 307 Temporary Redirect to GitHub
    return RedirectResponse(
        url=authorize_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.get("/github/callback")
async def github_callback(
    request: Request,
    response: Response,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    format: str | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    """Exchange GitHub OAuth authorization code server-side with PKCE and establish CortexForge session.

    Handles both automatic Sign Up (new user) and Sign In (existing user) by
    stable GitHub numeric user ID.
    """
    client_ip = _get_client_ip(request)

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"GitHub authorization error: {error_description or error}",
        )
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code parameter.",
        )
    if not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing OAuth state parameter.",
        )

    # Verify and retrieve state transaction from DB
    now = datetime.now(UTC)
    tx_res = await session.execute(
        select(OAuthTransaction).where(
            OAuthTransaction.provider == "github",
            OAuthTransaction.state == state,
        )
    )
    oauth_tx = tx_res.scalars().first()

    if not oauth_tx:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or unrecognized OAuth state parameter.",
        )
    if oauth_tx.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state has already been used. Replay detected.",
        )
    if _is_expired(oauth_tx.expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state has expired. Please re-authenticate.",
        )

    # Check cookie state if present (defense-in-depth CSRF verification)
    cookie_state = request.cookies.get("cortex_oauth_state")
    if cookie_state and cookie_state != state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state cookie mismatch. Request rejected for security.",
        )

    # Single-use enforcement: mark state as used immediately
    oauth_tx.used_at = now
    await session.commit()

    code_verifier = oauth_tx.code_verifier
    redirect_uri = oauth_tx.redirect_uri

    config = get_github_oauth_config(request)

    # Server-side token exchange and identity retrieval
    import httpx

    # Check if in simulated/mock test mode with mock_ code
    if (
        code.startswith("mock_")
        and not config.is_production
        and os.environ.get("PYTEST_CURRENT_TEST")
    ):
        raw_numeric_id = code.replace("mock_", "").replace("github_user_", "")
        gh_user_id = raw_numeric_id if raw_numeric_id.isdigit() else "10001"
        gh_login = f"dev_{gh_user_id[:8]}"
        gh_name = f"Developer {gh_login}"
        gh_email = f"{gh_login}@users.noreply.github.com"
        gh_avatar = f"https://avatars.githubusercontent.com/u/{gh_user_id}"
    else:
        # Live exchange
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": "CortexForge-OAuth",
                },
            )
            if token_resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to communicate with GitHub token endpoint.",
                )
            token_data = token_resp.json()
            if "error" in token_data:
                err_desc = token_data.get("error_description") or token_data["error"]
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"GitHub token exchange failed: {err_desc}",
                )
            access_token = token_data.get("access_token")
            if not access_token:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No access token returned by GitHub.",
                )

            # Fetch GitHub user profile
            user_resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "CortexForge-OAuth",
                },
            )
            if user_resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to retrieve GitHub user profile.",
                )
            user_data = user_resp.json()
            raw_id = user_data.get("id")
            if not raw_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid GitHub user data: missing numeric user id.",
                )
            gh_user_id = str(raw_id)
            gh_login = user_data.get("login") or f"gh_{gh_user_id}"
            gh_name = user_data.get("name") or gh_login
            gh_avatar = user_data.get("avatar_url")
            gh_email = user_data.get("email")

            # If email is private or not present in /user, fetch from /user/emails
            if not gh_email:
                emails_resp = await client.get(
                    "https://api.github.com/user/emails",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "CortexForge-OAuth",
                    },
                )
                if emails_resp.status_code == 200:
                    emails_list = emails_resp.json()
                    if isinstance(emails_list, list):
                        # Prioritize primary verified email
                        for em in emails_list:
                            if em.get("primary") and em.get("verified"):
                                gh_email = em.get("email")
                                break
                        # Fallback to any verified email
                        if not gh_email:
                            for em in emails_list:
                                if em.get("verified"):
                                    gh_email = em.get("email")
                                    break

            # Ultimate fallback: GitHub privacy no-reply email
            if not gh_email:
                gh_email = f"{gh_user_id}+{gh_login}@users.noreply.github.com"

    # Match user by stable numeric github_user_id
    u_stmt = select(User).where(User.github_user_id == gh_user_id)
    u_res = await session.execute(u_stmt)
    user = u_res.scalars().first()

    # Check ExternalIdentity table as secondary lookup for backward compatibility
    if not user:
        ext_res = await session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == "github",
                ExternalIdentity.provider_subject == gh_user_id,
            )
        )
        existing_ext = ext_res.scalars().first()
        if existing_ext:
            user = await session.get(User, existing_ext.user_id)

    audit_action = "USER_GITHUB_SIGNIN"

    if user:
        # Existing user: SIGN IN flow
        if user.status != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is suspended or inactive.",
            )
        user.github_user_id = gh_user_id
        user.github_login = gh_login
        if gh_avatar:
            user.avatar_url = gh_avatar
        if not user.display_name and gh_name:
            user.display_name = gh_name
        user.last_login_at = now
    else:
        # New user: SIGN UP flow
        # Check if email is already registered via password/OTP
        em_res = await session.execute(
            select(User).where(func.lower(User.email) == gh_email.lower().strip())
        )
        existing_email_user = em_res.scalars().first()

        if existing_email_user:
            # Link GitHub account to existing email user
            user = existing_email_user
            user.github_user_id = gh_user_id
            user.github_login = gh_login
            if gh_avatar:
                user.avatar_url = gh_avatar
            user.last_login_at = now
            audit_action = "USER_GITHUB_LINK"
        else:
            # Create brand new user
            user_id = str(uuid.uuid4())
            user = User(
                id=user_id,
                github_user_id=gh_user_id,
                github_login=gh_login,
                avatar_url=gh_avatar,
                display_name=gh_name,
                email=gh_email.lower().strip(),
                status="ACTIVE",
                email_verified_at=now,
                created_at=now,
                updated_at=now,
                last_login_at=now,
            )
            session.add(user)
            await session.flush()
            audit_action = "USER_GITHUB_SIGNUP"

    # Sync ExternalIdentity table
    ext_stmt = select(ExternalIdentity).where(
        ExternalIdentity.provider == "github",
        ExternalIdentity.provider_subject == gh_user_id,
    )
    ext_res = await session.execute(ext_stmt)
    ext_record = ext_res.scalars().first()
    if not ext_record:
        ext_record = ExternalIdentity(
            user_id=user.id,
            provider="github",
            provider_subject=gh_user_id,
            provider_email=gh_email,
            metadata_json={"login": gh_login},
            created_at=now,
        )
        session.add(ext_record)
    else:
        ext_record.provider_email = gh_email
        ext_record.metadata_json = {"login": gh_login}

    # Create server-side CortexForge session (separate from GitHub token)
    raw_token = generate_session_token()
    token_h = hash_token(raw_token)
    user_session = UserSession(
        user_id=user.id,
        session_token_hash=token_h,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip,
        expires_at=now + timedelta(days=SESSION_DURATION_DAYS),
        created_at=now,
    )
    session.add(user_session)

    await session.commit()
    await session.refresh(user)

    await AuditService.record(
        db_session=session,
        action=audit_action,
        target_type="user",
        target_id=user.id,
        user_id=user.id,
        actor_type="USER",
        details={
            "provider": "github",
            "github_user_id": gh_user_id,
            "github_login": gh_login,
        },
    )

    # Set secure HttpOnly cookie
    _set_session_cookie(response, raw_token, request)
    # Delete the temporary OAuth state cookie
    response.delete_cookie(key="cortex_oauth_state", path="/")

    # If client accepts JSON or is an API client / test runner without text/html
    accept_hdr = request.headers.get("accept", "")
    wants_redirect = "text/html" in accept_hdr and format != "json"
    if not wants_redirect:
        return AuthResponse(
            user=UserRead.model_validate(user),
            token=raw_token,
            message=(
                "GitHub authentication successful"
                if audit_action != "USER_GITHUB_SIGNUP"
                else "GitHub registration successful"
            ),
        )

    # Default browser behavior: 303 See Other redirect to dashboard
    redirect_target = os.environ.get("FRONTEND_URL", "/dashboard")
    red_resp = RedirectResponse(
        url=redirect_target, status_code=status.HTTP_303_SEE_OTHER
    )
    is_secure = (
        request.url.scheme == "https"
        or os.environ.get("ENVIRONMENT") == "production"
        or os.environ.get("CORTEX_ENV") == "production"
    )
    red_resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=int(timedelta(days=SESSION_DURATION_DAYS).total_seconds()),
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
    )
    red_resp.set_cookie(
        key="cortexforge_session",
        value=raw_token,
        max_age=int(timedelta(days=SESSION_DURATION_DAYS).total_seconds()),
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
    )
    red_resp.delete_cookie(key="cortex_oauth_state", path="/")
    return red_resp


# ==============================================================================
# User Profile, Current State & Logout
# ==============================================================================


@router.get("/me", response_model=AuthMeResponse)
async def get_current_user_profile(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> AuthMeResponse:
    """Retrieve profile and authentication status of current authenticated user."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )

    user = await session.get(User, principal.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account does not exist or has been revoked.",
        )

    # Check connected providers
    gh_res = await session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.user_id == user.id,
            ExternalIdentity.provider == "github",
        )
    )
    github_connected = (
        gh_res.scalars().first() is not None or user.github_user_id is not None
    )

    pwd_res = await session.execute(
        select(PasswordCredential).where(PasswordCredential.user_id == user.id)
    )
    has_password = pwd_res.scalars().first() is not None

    return AuthMeResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        github_user_id=user.github_user_id,
        github_login=user.github_login,
        avatar_url=user.avatar_url,
        user=UserRead.model_validate(user),
        github_connected=github_connected,
        has_password=has_password,
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Invalidate current session and clear session cookie."""
    token = get_session_token_from_request(request)
    now = datetime.now(UTC)
    if token:
        token_h = hash_token(token)
        res = await session.execute(
            select(UserSession).where(UserSession.session_token_hash == token_h)
        )
        user_sess = res.scalars().first()
        if user_sess:
            user_sess.revoked_at = now
            await session.commit()

    if principal.user_id:
        await AuditService.record(
            db_session=session,
            action="USER_LOGOUT",
            target_type="user",
            target_id=principal.user_id,
            user_id=principal.user_id,
            actor_type="USER",
        )

    _clear_session_cookie(response)
    return {"message": "Logged out successfully."}


# ==============================================================================
# Password Reset & Password Change
# ==============================================================================


class PasswordResetRequestResponse(BaseModel):
    message: str
    debug_token: str | None = None


@router.post("/password/reset-request", response_model=PasswordResetRequestResponse)
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> PasswordResetRequestResponse:
    """Request a single-use cryptographic password reset token."""
    client_ip = _get_client_ip(request)
    if not auth_rate_limiter.check(
        "pwd_reset", client_ip, max_requests=5, window_seconds=60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many password reset requests. Please try again later.",
        )

    norm_email = payload.email.strip().lower()
    res = await session.execute(
        select(User).where(func.lower(User.email) == norm_email)
    )
    user = res.scalars().first()

    raw_token: str | None = None
    if user and user.status == "ACTIVE":
        raw_token = secrets.token_urlsafe(32)
        token_h = hash_token(raw_token)
        now = datetime.now(UTC)

        reset_record = PasswordResetToken(
            user_id=user.id,
            token_hash=token_h,
            expires_at=now + timedelta(hours=1),
            created_at=now,
        )
        session.add(reset_record)
        await session.commit()

        await AuditService.record(
            db_session=session,
            action="USER_PASSWORD_RESET_REQUESTED",
            target_type="user",
            target_id=user.id,
            user_id=user.id,
            actor_type="USER",
        )

    is_test_mode = _is_server_debug_mode_allowed()

    return PasswordResetRequestResponse(
        message="If this email is registered, password reset instructions have been sent.",
        debug_token=raw_token if (is_test_mode and raw_token) else None,
    )


@router.post("/password/reset")
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Verify reset token, update password, and invalidate all existing sessions."""
    now = datetime.now(UTC)
    token_h = hash_token(payload.token.strip())

    res = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_h,
            PasswordResetToken.consumed_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
    )
    record = res.scalars().first()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset link is invalid or has expired.",
        )

    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters long.",
        )

    record.consumed_at = now

    # Update password credential
    cred_res = await session.execute(
        select(PasswordCredential).where(PasswordCredential.user_id == record.user_id)
    )
    cred = cred_res.scalars().first()
    new_hash = PasswordHasher.hash(payload.new_password)

    if cred:
        cred.password_hash = new_hash
        cred.updated_at = now
    else:
        cred = PasswordCredential(
            user_id=record.user_id,
            password_hash=new_hash,
            algorithm="scrypt",
            created_at=now,
            updated_at=now,
        )
        session.add(cred)

    # Invalidate all active sessions for this user (security requirement §8, §4)
    active_sessions = await session.execute(
        select(UserSession).where(
            UserSession.user_id == record.user_id,
            UserSession.revoked_at.is_(None),
        )
    )
    for s in active_sessions.scalars().all():
        s.revoked_at = now

    await session.commit()

    await AuditService.record(
        db_session=session,
        action="USER_PASSWORD_RESET_COMPLETED",
        target_type="user",
        target_id=record.user_id,
        user_id=record.user_id,
        actor_type="USER",
    )

    return {
        "message": "Password updated successfully. Please sign in with your new password."
    }


@router.post("/password/change")
async def change_password(
    payload: PasswordChangeRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Change password for the current authenticated user."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated."
        )

    now = datetime.now(UTC)
    cred_res = await session.execute(
        select(PasswordCredential).where(
            PasswordCredential.user_id == principal.user_id
        )
    )
    cred = cred_res.scalars().first()
    if not cred or not PasswordHasher.verify(
        payload.current_password, cred.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 8 characters long.",
        )

    cred.password_hash = PasswordHasher.hash(payload.new_password)
    cred.updated_at = now
    await session.commit()

    await AuditService.record(
        db_session=session,
        action="USER_PASSWORD_CHANGED",
        target_type="user",
        target_id=principal.user_id,
        user_id=principal.user_id,
        actor_type="USER",
    )

    return {"message": "Password changed successfully."}


# ==============================================================================
# Active Sessions Management
# ==============================================================================


@router.get("/sessions", response_model=list[SessionRead])
async def list_active_sessions(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[SessionRead]:
    """List active sessions for current authenticated user."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated."
        )

    now = datetime.now(UTC)
    curr_token = get_session_token_from_request(request)
    curr_hash = hash_token(curr_token) if curr_token else None

    res = await session.execute(
        select(UserSession)
        .where(
            UserSession.user_id == principal.user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .order_by(UserSession.created_at.desc())
    )
    results: list[SessionRead] = []
    for s in res.scalars().all():
        results.append(
            SessionRead(
                id=s.id,
                user_id=s.user_id,
                user_agent=s.user_agent,
                ip_address=s.ip_address,
                expires_at=s.expires_at,
                created_at=s.created_at,
                is_current=(s.session_token_hash == curr_hash),
            )
        )
    return results


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Revoke a specific active session."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated."
        )

    target = await session.get(UserSession, session_id)
    if not target or target.user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )

    target.revoked_at = datetime.now(UTC)
    await session.commit()
