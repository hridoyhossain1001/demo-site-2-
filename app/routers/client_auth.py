from datetime import datetime, timedelta, timezone
import os
import re
import secrets
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.client import Client
from app.models.client_session import ClientSession
from app.models.client_user import ClientUser
from app.security import encrypt_token, decrypt_token
from app.services.auth_service import (
    hash_password,
    hash_session_token,
    new_session_token,
    normalize_email,
    verify_password,
)
from app.services.client_secrets import ensure_capi_signing_secret
from app.services.plan_service import new_free_values, new_trial_values, plan_summary, record_trial_identity, require_trial_available
from app.limiter import limiter


router = APIRouter()

CLIENT_SESSION_COOKIE = "buykori_client_session"
CLIENT_CSRF_COOKIE = "buykori_client_csrf"
CLIENT_CSRF_HEADER = "X-Client-CSRF-Token"
SESSION_DAYS = int(os.getenv("CLIENT_SESSION_DAYS", "30"))
COOKIE_DOMAIN = os.getenv("CLIENT_COOKIE_DOMAIN", ".buykori.app")
COOKIE_SECURE = os.getenv("CLIENT_COOKIE_SECURE", "true").lower() in ("true", "1", "yes")
COOKIE_SAMESITE = os.getenv("CLIENT_COOKIE_SAMESITE", "lax")
ALLOWED_CLIENT_AUTH_HOSTS = {
    host.strip().lower()
    for host in os.getenv(
        "CLIENT_AUTH_ALLOWED_HOSTS",
        "buykori.app,www.buykori.app,client.buykori.app,localhost,127.0.0.1",
    ).split(",")
    if host.strip()
}


class ClientSignupRequest(BaseModel):
    full_name: str
    email: str
    phone_number: str
    password: str
    business_name: str
    domain: str | None = None
    selected_plan: Literal["free", "growth_trial"] = "growth_trial"


class ClientLoginRequest(BaseModel):
    email: str
    password: str


def _clean_name(value: str, field_name: str) -> str:
    value = value.strip()
    if not value or len(value) > 120:
        raise HTTPException(status_code=400, detail=f"{field_name} must be 1-120 characters.")
    return value


def _clean_domain(domain: str | None) -> str | None:
    if not domain or not domain.strip():
        return None
    raw = domain.strip().lower()
    raw = re.sub(r"^https?://", "", raw).split("/", 1)[0].strip().rstrip(".")
    if raw.startswith("www."):
        raw = raw[4:]
    if not raw or len(raw) > 255 or "." not in raw:
        raise HTTPException(status_code=400, detail="Website domain must look like example.com.")
    return raw


def _validate_email(email: str) -> str:
    email = normalize_email(email)
    if len(email) > 255 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return email


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")


def _validate_phone_number(phone_number: str) -> str:
    raw = str(phone_number or "").strip()
    digits = re.sub(r"\D+", "", raw)
    if digits.startswith("8801") and len(digits) == 13:
        return f"+{digits}"
    if digits.startswith("01") and len(digits) == 11:
        return f"+88{digits}"
    if digits.startswith("1") and len(digits) == 10:
        return f"+880{digits}"
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return f"+{digits}"
    if 8 <= len(digits) <= 15 and not digits.startswith("0"):
        return f"+{digits}"
    raise HTTPException(status_code=400, detail="Enter a valid phone number for trial support.")


def require_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    cookie_authenticated_mutation = (
        request.cookies.get(CLIENT_SESSION_COOKIE)
        and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    )
    if not origin and cookie_authenticated_mutation:
        raise HTTPException(status_code=403, detail="Origin is required for cookie-authenticated requests.")
    if not origin:
        return
    host = (urlparse(origin).hostname or "").lower()
    request_host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.hostname
        or ""
    ).split(",", 1)[0].split(":", 1)[0].strip().lower()
    if host != request_host and host not in ALLOWED_CLIENT_AUTH_HOSTS:
        raise HTTPException(status_code=403, detail="Origin is not allowed.")
    if cookie_authenticated_mutation:
        csrf_cookie = request.cookies.get(CLIENT_CSRF_COOKIE) or ""
        csrf_header = request.headers.get(CLIENT_CSRF_HEADER) or ""
        if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
            raise HTTPException(status_code=403, detail="Client CSRF token is missing or invalid.")


def _get_cookie_domain(request: Request) -> str | None:
    env_val = os.getenv("CLIENT_COOKIE_DOMAIN")
    if env_val is not None:
        val = env_val.strip()
        if val.lower() in ("none", "null", "false", ""):
            return None
        return val
        
    host = (request.url.hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1"):
        return None
        
    parts = host.split(".")
    if len(parts) >= 2:
        if parts[0] in ("api", "client", "track", "admin", "www"):
            return "." + ".".join(parts[1:])
        return "." + host
    return None


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    encrypted = encrypt_token(token)
    domain = _get_cookie_domain(request)
    response.set_cookie(
        key=CLIENT_SESSION_COOKIE,
        value=encrypted,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        domain=domain,
        path="/",
    )


def _set_csrf_cookie(response: Response, request: Request) -> str:
    token = secrets.token_urlsafe(32)
    domain = _get_cookie_domain(request)
    cookie_kwargs = dict(
        key=CLIENT_CSRF_COOKIE,
        value=token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(**cookie_kwargs, domain=domain)
    if domain:
        response.set_cookie(**cookie_kwargs, domain=None)
    return token


def _clear_session_cookie(response: Response, request: Request) -> None:
    domain = _get_cookie_domain(request)
    response.delete_cookie(
        key=CLIENT_SESSION_COOKIE,
        domain=domain,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite=COOKIE_SAMESITE,
    )
    response.delete_cookie(
        key=CLIENT_CSRF_COOKIE,
        domain=domain,
        path="/",
        secure=COOKIE_SECURE,
        httponly=False,
        samesite=COOKIE_SAMESITE,
    )
    if domain:
        response.delete_cookie(
            key=CLIENT_CSRF_COOKIE,
            path="/",
            secure=COOKIE_SECURE,
            httponly=False,
            samesite=COOKIE_SAMESITE,
        )


def _user_payload(user: ClientUser, client: Client) -> dict:
    plan = plan_summary(client)
    return {
        "id": user.id,
        "email": user.email,
        "phone_number": user.phone_number,
        "full_name": user.full_name,
        "role": user.role,
        "email_verified": bool(user.email_verified),
        "client": {
            "id": client.id,
            "name": client.name,
            "domain": client.domain,
            "is_active": bool(client.is_active),
            "plan": plan,
        },
    }


async def _create_session(db: AsyncSession, user: ClientUser, response: Response, request: Request) -> None:
    token = new_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    db.add(ClientSession(
        user_id=user.id,
        client_id=user.client_id,
        token_hash=hash_session_token(token),
        expires_at=expires_at,
    ))
    _set_session_cookie(response, token, request)
    _set_csrf_cookie(response, request)


async def get_client_user_from_cookie(
    request: Request,
    db: AsyncSession,
) -> tuple[ClientUser, Client, ClientSession]:
    encrypted = request.cookies.get(CLIENT_SESSION_COOKIE)
    if not encrypted:
        raise HTTPException(status_code=401, detail="Not signed in")
    try:
        token = decrypt_token(encrypted, allow_legacy_plaintext=False)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session")

    session_r = await db.execute(
        select(ClientSession).where(ClientSession.token_hash == hash_session_token(token))
    )
    session = session_r.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    expires_at = session.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if session.revoked_at or expires_at < now:
        raise HTTPException(status_code=401, detail="Invalid session")

    user_r = await db.execute(select(ClientUser).where(ClientUser.id == session.user_id))
    user = user_r.scalar_one_or_none()
    client_r = await db.execute(select(Client).where(Client.id == session.client_id))
    client = client_r.scalar_one_or_none()
    if not user or not user.is_active or not client or not client.is_active:
        raise HTTPException(status_code=401, detail="Inactive account")
    return user, client, session


@router.post("/auth/client/signup")
@limiter.limit("5/minute")
async def client_signup(
    payload: ClientSignupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    require_allowed_origin(request)
    email = _validate_email(payload.email)
    _validate_password(payload.password)
    full_name = _clean_name(payload.full_name, "Full name")
    phone_number = _validate_phone_number(payload.phone_number)
    business_name = _clean_name(payload.business_name, "Business name")
    domain = _clean_domain(payload.domain)

    existing = await db.execute(select(ClientUser).where(ClientUser.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="An account already exists for this email.")
    starts_trial = payload.selected_plan == "growth_trial"
    if starts_trial:
        await require_trial_available(db, domain=domain)

    client = Client(
        name=business_name,
        pixel_id="0",
        access_token="",
        domain=domain,
        api_key=os.urandom(24).hex(),
        public_key=os.urandom(18).hex(),
        portal_key=os.urandom(18).hex(),
        enable_facebook=False,
        enable_tiktok=False,
        enable_ga4=False,
        daily_quota=1000,
        rate_limit=120,
        **(new_trial_values() if starts_trial else new_free_values()),
    )
    db.add(client)
    await db.flush()
    ensure_capi_signing_secret(client)

    user = ClientUser(
        client_id=client.id,
        email=email,
        phone_number=phone_number,
        password_hash=hash_password(payload.password),
        full_name=full_name,
        role="owner",
        is_active=True,
        email_verified=False,
    )
    db.add(user)
    await db.flush()
    if starts_trial:
        await record_trial_identity(db, client, email=email, source="signup")
    await _create_session(db, user, response, request)
    await db.commit()
    await db.refresh(client)
    await db.refresh(user)
    return {"status": "success", "user": _user_payload(user, client)}


@router.post("/auth/client/login")
@limiter.limit("10/minute")
async def client_login(
    payload: ClientLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    require_allowed_origin(request)
    email = _validate_email(payload.email)
    user_r = await db.execute(select(ClientUser).where(ClientUser.email == email))
    user = user_r.scalar_one_or_none()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    client_r = await db.execute(select(Client).where(Client.id == user.client_id))
    client = client_r.scalar_one_or_none()
    if not client or not client.is_active:
        raise HTTPException(status_code=401, detail="Inactive workspace.")

    user.last_login_at = datetime.now(timezone.utc)
    await _create_session(db, user, response, request)
    await db.commit()
    return {"status": "success", "user": _user_payload(user, client)}


@router.post("/auth/client/logout")
async def client_logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    require_allowed_origin(request)
    encrypted = request.cookies.get(CLIENT_SESSION_COOKIE)
    if encrypted:
        try:
            token = decrypt_token(encrypted, allow_legacy_plaintext=False)
            session_r = await db.execute(
                select(ClientSession).where(ClientSession.token_hash == hash_session_token(token))
            )
            session = session_r.scalar_one_or_none()
            if session and not session.revoked_at:
                session.revoked_at = datetime.now(timezone.utc)
                await db.commit()
        except Exception:
            pass
    _clear_session_cookie(response, request)
    return {"status": "success"}


@router.get("/auth/client/me")
async def client_me(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    require_allowed_origin(request)
    user, client, _ = await get_client_user_from_cookie(request, db)
    _set_csrf_cookie(response, request)
    return {"status": "success", "user": _user_payload(user, client)}
