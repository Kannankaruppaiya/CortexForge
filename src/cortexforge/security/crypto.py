"""Cryptographic operations and secure password hashing for CortexForge.

Requirements:
- Never store plaintext passwords.
- Use a modern memory-hard password hashing algorithm (Argon2id if installed, native Scrypt fallback).
- Unique cryptographically secure salt per password.
- Generic, constant-time comparison to protect against timing attacks.
- Cryptographically random OTP generation with attempt bounds.
"""

import base64
import hashlib
import hmac
import secrets

# Try importing argon2-cffi if installed; otherwise use standard library hashlib.scrypt
_ARGON2_AVAILABLE = False
try:
    import argon2

    _argon2_hasher = argon2.PasswordHasher(
        time_cost=2,
        memory_cost=65536,  # 64 MB
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )
    _ARGON2_AVAILABLE = True
except ImportError:
    _argon2_hasher = None  # type: ignore[assignment]


class PasswordHasher:
    """Secure password hashing using Argon2id when available, or hashlib.scrypt natively."""

    @classmethod
    def hash_password(cls, password: str) -> str:
        """Hash a plaintext password with a unique random salt."""
        if not password or len(password) < 8:
            raise ValueError("Password must be at least 8 characters in length.")

        if _ARGON2_AVAILABLE and _argon2_hasher is not None:
            return _argon2_hasher.hash(password)

        # Standard library Scrypt (memory-hard, built into Python 3.8+)
        # Parameters: N=16384 (16MB), r=8, p=1
        salt = secrets.token_bytes(16)
        n, r, p = 16384, 8, 1
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=64 * 1024 * 1024,
        )
        salt_b64 = base64.b64encode(salt).decode("ascii")
        hash_b64 = base64.b64encode(derived).decode("ascii")
        return f"$scrypt$ln=14,r={r},p={p}${salt_b64}${hash_b64}"

    @classmethod
    def hash(cls, password: str) -> str:
        """Alias for hash_password."""
        return cls.hash_password(password)

    @classmethod
    def verify(cls, password: str, hashed: str) -> bool:
        """Alias for verify_password."""
        return cls.verify_password(password, hashed)

    @classmethod
    def verify_password(cls, password: str, hashed: str) -> bool:
        """Verify a plaintext password against a stored hash in constant time."""
        if not password or not hashed:
            return False

        try:
            if hashed.startswith("$argon2"):
                if _ARGON2_AVAILABLE and _argon2_hasher is not None:
                    _argon2_hasher.verify(hashed, password)
                    return True
                return False

            if hashed.startswith("$scrypt$"):
                # Format: $scrypt$ln=14,r=8,p=1$<salt_b64>$<hash_b64>
                parts = hashed.split("$")
                if len(parts) != 5:
                    return False
                params_str = parts[2]
                salt_b64 = parts[3]
                expected_hash_b64 = parts[4]

                params: dict[str, int] = {}
                for item in params_str.split(","):
                    k, v = item.split("=")
                    params[k] = int(v)

                n = 1 << params.get("ln", 14)
                r = params.get("r", 8)
                p = params.get("p", 1)

                salt = base64.b64decode(salt_b64.encode("ascii"))
                expected_hash = base64.b64decode(expected_hash_b64.encode("ascii"))

                actual_hash = hashlib.scrypt(
                    password.encode("utf-8"),
                    salt=salt,
                    n=n,
                    r=r,
                    p=p,
                    maxmem=64 * 1024 * 1024,
                )
                return hmac.compare_digest(actual_hash, expected_hash)

            return False
        except Exception:
            return False


def generate_otp(length: int = 6) -> str:
    """Generate a cryptographically secure numeric one-time password."""
    max_val = 10**length
    num = secrets.randbelow(max_val)
    return str(num).zfill(length)


def hash_token(token: str) -> str:
    """Compute SHA-256 digest of token/OTP for safe storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    """Generate a high-entropy 256-bit URL-safe session token."""
    return secrets.token_urlsafe(32)


def generate_agent_key() -> tuple[str, str]:
    """Generate a prefixed API key for AI Agents and return (raw_key, key_hash)."""
    raw_key = f"cortex_agent_{secrets.token_urlsafe(32)}"
    key_hash = hash_token(raw_key)
    return raw_key, key_hash


def _get_encryption_key(explicit_key: str | bytes | None = None) -> bytes:
    """Derive 256-bit encryption key from CORTEX_GITHUB_TOKEN_ENCRYPTION_KEY or SECRET_KEY."""
    import os

    if explicit_key:
        raw = (
            explicit_key.encode("utf-8")
            if isinstance(explicit_key, str)
            else explicit_key
        )
    else:
        raw = (
            os.environ.get("CORTEX_GITHUB_TOKEN_ENCRYPTION_KEY")
            or os.environ.get("CORTEX_ENCRYPTION_KEY")
            or os.environ.get("SECRET_KEY")
            or "cortexforge_default_envelope_key_do_not_use_in_production"
        ).encode("utf-8")
    return hashlib.sha256(raw).digest()


def encrypt_token(plaintext: str, key: str | bytes | None = None) -> str:
    """Encrypt a sensitive token at rest using AES-256-GCM.

    Returns URL-safe string prefixed with 'v1:enc:' followed by base64-encoded nonce+ciphertext.
    """
    if not plaintext:
        return ""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aes_key = _get_encryption_key(key)
    aesgcm = AESGCM(aes_key)
    nonce = secrets.token_bytes(12)  # 96-bit standard GCM nonce
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    encoded = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return f"v1:enc:{encoded}"


def decrypt_token(ciphertext: str, key: str | bytes | None = None) -> str:
    """Decrypt a token encrypted with encrypt_token.

    If the token does not have 'v1:enc:' prefix, it is treated as a legacy plaintext token
    for seamless backward compatibility.
    """
    if not ciphertext:
        return ""
    if not ciphertext.startswith("v1:enc:"):
        # Legacy unencrypted token
        return ciphertext

    raw_b64 = ciphertext[7:]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aes_key = _get_encryption_key(key)
    aesgcm = AESGCM(aes_key)
    try:
        combined = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
        if len(combined) < 12:
            raise ValueError("Invalid encrypted payload length")
        nonce = combined[:12]
        ct = combined[12:]
        decrypted = aesgcm.decrypt(nonce, ct, None)
        return decrypted.decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Failed to decrypt token: {exc}") from exc

