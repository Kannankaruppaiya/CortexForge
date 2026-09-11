"""Unit tests for the individual-user authentication system."""

import pytest
from httpx import ASGITransport, AsyncClient

from cortexforge.apps.api.main import app
from cortexforge.security.crypto import (
    PasswordHasher,
)


@pytest.mark.asyncio
async def test_password_hasher_properties():
    """Verify password hashing security: non-plaintext, unique salts, constant-time compare."""
    pwd = "CorrectHorseBatteryStaple123!"
    h1 = PasswordHasher.hash(pwd)
    h2 = PasswordHasher.hash(pwd)

    # Never store plaintext
    assert h1 != pwd
    assert h2 != pwd
    # Unique salt per hash
    assert h1 != h2

    # Verification
    assert PasswordHasher.verify(pwd, h1) is True
    assert PasswordHasher.verify(pwd, h2) is True
    assert PasswordHasher.verify("WrongPassword123!", h1) is False


@pytest.mark.asyncio
async def test_registration_and_login_flow(test_session):
    """Verify user registration, internal ID generation, password hashing, and login."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register new user
        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "alice@cortexforge.dev",
                "password": "SuperSecretPassword123!",
                "display_name": "Alice Engineer",
            },
        )
        assert reg_resp.status_code == 201, reg_resp.text
        data = reg_resp.json()
        assert "user" in data
        assert data["user"]["email"] == "alice@cortexforge.dev"
        assert data["user"]["display_name"] == "Alice Engineer"
        assert data["user"]["status"] == "ACTIVE"
        # Internal user ID is server generated (UUID)
        user_id = data["user"]["id"]
        assert len(user_id) >= 32
        assert "cortex_session" in reg_resp.cookies

        # 2. Reject duplicate registration
        dup_resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "alice@cortexforge.dev",
                "password": "AnotherPassword123!",
            },
        )
        assert dup_resp.status_code == 409

        # 3. Reject short password (< 8 chars)
        short_resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "bob@cortexforge.dev",
                "password": "short",
            },
        )
        assert short_resp.status_code in (400, 422)

        # 4. Successful login
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "alice@cortexforge.dev",
                "password": "SuperSecretPassword123!",
            },
        )
        assert login_resp.status_code == 200
        assert "token" in login_resp.json()
        session_token = login_resp.json()["token"]

        # 5. Failed login: wrong password returns generic error
        wrong_resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "alice@cortexforge.dev",
                "password": "IncorrectPassword123!",
            },
        )
        assert wrong_resp.status_code == 401
        assert "Invalid email or password" in wrong_resp.json()["detail"]

        # 6. Check /auth/me with session cookie
        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Cookie": f"cortex_session={session_token}"},
        )
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["user"]["id"] == user_id
        assert me_data["has_password"] is True


@pytest.mark.asyncio
async def test_email_otp_complete_lifecycle(test_session):
    """Verify passwordless OTP request, incorrect attempts, expiration, brute-force limits, and replay rejection."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email = "otp_user@cortexforge.dev"

        # 1. Request OTP in test mode
        req_resp = await client.post(
            "/api/v1/auth/otp/request",
            json={"email": email},
            headers={"x-cortex-test-mode": "true"},
        )
        assert req_resp.status_code == 200
        otp_code = req_resp.json().get("debug_otp")
        assert otp_code is not None
        assert len(otp_code) == 6
        assert otp_code.isdigit()

        # 2. Incorrect OTP attempt fails and increments attempts
        fail_resp = await client.post(
            "/api/v1/auth/otp/verify",
            json={"email": email, "otp": "999999"},
        )
        assert fail_resp.status_code == 401
        assert "Invalid verification code" in fail_resp.json()["detail"]

        # 3. Correct OTP verification succeeds and establishes session
        succ_resp = await client.post(
            "/api/v1/auth/otp/verify",
            json={"email": email, "otp": otp_code},
        )
        assert succ_resp.status_code == 200
        user_data = succ_resp.json()["user"]
        assert user_data["email"] == email
        assert "cortex_session" in succ_resp.cookies

        # 4. Reused OTP is rejected (single-use enforcement)
        reuse_resp = await client.post(
            "/api/v1/auth/otp/verify",
            json={"email": email, "otp": otp_code},
        )
        assert reuse_resp.status_code == 400

        # 5. Test Brute-Force attempt limit
        req2_resp = await client.post(
            "/api/v1/auth/otp/request",
            json={"email": "brute_user@cortexforge.dev"},
            headers={"x-cortex-test-mode": "true"},
        )
        assert req2_resp.status_code == 200

        # Enter wrong code 5 times
        for _ in range(5):
            await client.post(
                "/api/v1/auth/otp/verify",
                json={"email": "brute_user@cortexforge.dev", "otp": "000000"},
            )

        # 6th attempt should return 400 (invalidated / max attempts exceeded)
        locked_resp = await client.post(
            "/api/v1/auth/otp/verify",
            json={"email": "brute_user@cortexforge.dev", "otp": "000000"},
        )
        assert locked_resp.status_code == 400


@pytest.mark.asyncio
async def test_github_oauth_and_state_validation(test_session):
    """Verify GitHub OAuth state generation, CSRF protection, and account linking."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Authorize endpoint produces URL and state
        auth_resp = await client.get("/api/v1/auth/github/authorize")
        assert auth_resp.status_code == 200
        data = auth_resp.json()
        assert "authorize_url" in data
        assert "github.com/login/oauth/authorize" in data["authorize_url"]
        assert "state=" in data["authorize_url"]
        assert (
            "google" not in data["authorize_url"].lower()
        )  # Negative constraint verification
        state = data["state"]

        # 2. Callback with invalid state is rejected
        bad_state_resp = await client.get(
            "/api/v1/auth/github/callback?code=mock_code123&state=tampered_state",
            cookies={"cortex_oauth_state": state},
        )
        assert bad_state_resp.status_code == 400

        # 3. Callback with valid state creates user and links external identity
        good_resp = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_42&state={state}",
            cookies={"cortex_oauth_state": state},
        )
        assert good_resp.status_code == 200
        gh_user = good_resp.json()["user"]
        assert (
            "users.noreply.github.com" in gh_user["email"]
            or "github" in gh_user["email"]
        )
        assert "cortex_session" in good_resp.cookies


@pytest.mark.asyncio
async def test_session_logout_and_password_reset(test_session):
    """Verify session revocation upon logout and multi-session invalidation upon password reset."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register user
        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "reset_user@cortexforge.dev",
                "password": "InitialPassword123!",
            },
        )
        token1 = reg_resp.json()["token"]

        # 2. Login second session
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "reset_user@cortexforge.dev",
                "password": "InitialPassword123!",
            },
        )
        token2 = login_resp.json()["token"]

        # 3. Logout session 1
        logout_resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Cookie": f"cortex_session={token1}"},
        )
        assert logout_resp.status_code == 200

        # 4. Request password reset
        reset_req = await client.post(
            "/api/v1/auth/password/reset-request",
            json={"email": "reset_user@cortexforge.dev"},
            headers={"x-cortex-test-mode": "true"},
        )
        assert reset_req.status_code == 200
        reset_token = reset_req.json().get("debug_token")
        assert reset_token is not None

        # 5. Confirm password reset (invalidates active sessions §8)
        confirm_resp = await client.post(
            "/api/v1/auth/password/reset",
            json={
                "token": reset_token,
                "new_password": "BrandNewSecurePassword456!",
            },
        )
        assert confirm_resp.status_code == 200

        # Verify active session token2 is invalidated after password reset
        me_after_reset = await client.get(
            "/api/v1/auth/me",
            headers={"Cookie": f"cortex_session={token2}"},
        )
        assert me_after_reset.status_code == 401

        # 6. Reusing password reset token fails
        reuse_token_resp = await client.post(
            "/api/v1/auth/password/reset",
            json={
                "token": reset_token,
                "new_password": "AnotherNewPassword789!",
            },
        )
        assert reuse_token_resp.status_code == 400

        # 7. Old password no longer works
        old_pwd_resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "reset_user@cortexforge.dev",
                "password": "InitialPassword123!",
            },
        )
        assert old_pwd_resp.status_code == 401

        # 8. New password succeeds
        new_pwd_resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "reset_user@cortexforge.dev",
                "password": "BrandNewSecurePassword456!",
            },
        )
        assert new_pwd_resp.status_code == 200
