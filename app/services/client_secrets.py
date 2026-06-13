import secrets

from app.security import decrypt_token, encrypt_token


def generate_capi_signing_secret() -> str:
    return secrets.token_urlsafe(32)


def decrypt_capi_signing_secret(value: str | None) -> str:
    if not value:
        return ""
    return decrypt_token(value, allow_legacy_plaintext=False)


def ensure_capi_signing_secret(client) -> str:
    existing = decrypt_capi_signing_secret(getattr(client, "capi_signing_secret", None))
    if existing:
        return existing

    secret = generate_capi_signing_secret()
    client.capi_signing_secret = encrypt_token(secret)
    return secret
