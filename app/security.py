"""
Token encryption helpers.

ENCRYPTION_KEY must be a valid Fernet key. Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
ALLOW_LEGACY_PLAINTEXT_TOKENS = os.getenv(
    "ALLOW_LEGACY_PLAINTEXT_TOKENS",
    "",
).lower() in ("true", "1", "yes")

FALLBACK_ENCRYPTION_KEYS = os.getenv("FALLBACK_ENCRYPTION_KEYS", "")
_fallback_fernets = []
if FALLBACK_ENCRYPTION_KEYS:
    for key in FALLBACK_ENCRYPTION_KEYS.split(","):
        k = key.strip()
        if k:
            try:
                _fallback_fernets.append(Fernet(k.encode()))
            except ValueError:
                logger.error("A fallback key in FALLBACK_ENCRYPTION_KEYS is invalid.")

if not ENCRYPTION_KEY:
    raise RuntimeError("ENCRYPTION_KEY environment variable is required.")

try:
    _fernet = Fernet(ENCRYPTION_KEY.encode())
except ValueError as exc:
    raise RuntimeError("ENCRYPTION_KEY must be a valid Fernet key.") from exc


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token for storage or a signed session cookie."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(encrypted: str, *, allow_legacy_plaintext: bool | None = None) -> str:
    """Decrypt a token and fail closed unless legacy fallback is explicitly requested."""
    try:
        return _fernet.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        for fallback in _fallback_fernets:
            try:
                return fallback.decrypt(encrypted.encode()).decode()
            except InvalidToken:
                pass

        app_env = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").lower()
        is_production = app_env in ("production", "prod") or bool(os.getenv("PRIMARY_DOMAIN"))
        allow_fallback = bool(allow_legacy_plaintext) and ALLOW_LEGACY_PLAINTEXT_TOKENS
        if is_production:
            allow_fallback = False

        if allow_fallback:
            logger.warning(
                "Token decryption failed; using legacy plaintext fallback. "
                "Disable ALLOW_LEGACY_PLAINTEXT_TOKENS after migration."
            )
            return encrypted
        raise
    except Exception:
        logger.warning("Token decryption failed because the encrypted value is malformed.")
        raise


PENDING_CREDENTIAL_VALUES = {"pending_setup"}


def encrypted_credential_is_configured(encrypted: str | None) -> bool:
    """Return true only when an encrypted field contains a real configured secret."""
    if not encrypted:
        return False
    try:
        plaintext = decrypt_token(encrypted, allow_legacy_plaintext=False).strip()
    except Exception:
        return False
    return bool(plaintext) and plaintext not in PENDING_CREDENTIAL_VALUES


def meta_credentials_configured(client) -> bool:
    """Check Meta readiness without treating setup placeholders as credentials."""
    pixel_id = str(getattr(client, "pixel_id", "") or "").strip()
    return pixel_id not in {"", "0"} and encrypted_credential_is_configured(
        getattr(client, "access_token", None)
    )
