from __future__ import annotations

import base64
import hashlib
import os
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import HTTPException, Request


TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{16,160}$")
PUBLIC_GATEWAY_BASE_URL = os.getenv("PUBLIC_GATEWAY_BASE_URL", "").rstrip("/")
ALLOWED_GATEWAY_HOSTS = {
    h.strip().lower()
    for h in os.getenv("ALLOWED_GATEWAY_HOSTS", "").split(",")
    if h.strip()
}
DEV_GATEWAY_HOSTS = {"localhost", "127.0.0.1", "testserver"}


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def validate_token(value: str, field_name: str) -> str:
    value = str(value or "").strip()
    if not TOKEN_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}.")
    return value


def normalize_site_url(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Site URL is required.")
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid site URL.")
    host = normalize_host(parsed.hostname)
    if parsed.scheme != "https" and host not in {"localhost", "127.0.0.1"}:
        raise HTTPException(status_code=400, detail="Site URL must use HTTPS.")
    netloc = parsed.netloc.lower()
    normalized = urlunparse((parsed.scheme.lower(), netloc, "", "", "", ""))
    return normalized.rstrip("/"), host


def normalize_host(host: str) -> str:
    clean = str(host or "").strip().lower().rstrip(".")
    if clean.startswith("www."):
        clean = clean[4:]
    if not clean:
        raise HTTPException(status_code=400, detail="Invalid site host.")
    return clean


def validate_return_url(value: str, expected_host: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid return URL.")
    return_host = normalize_host(parsed.hostname)
    if return_host != expected_host:
        raise HTTPException(status_code=400, detail="Return URL must match the WordPress site.")
    if parsed.scheme != "https" and return_host not in {"localhost", "127.0.0.1"}:
        raise HTTPException(status_code=400, detail="Return URL must use HTTPS.")
    return raw


def is_site_allowed_for_client(site_host: str, client_domain: str | None) -> bool:
    if not client_domain:
        return True
    allowed_domains = [normalize_host(part) for part in client_domain.split(",") if part.strip()]
    for allowed in allowed_domains:
        if site_host == allowed or site_host.endswith("." + allowed):
            return True
    return False


def append_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def gateway_url_from_request(request: Request) -> str:
    if PUBLIC_GATEWAY_BASE_URL:
        parsed = urlparse(PUBLIC_GATEWAY_BASE_URL)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=500, detail="Invalid PUBLIC_GATEWAY_BASE_URL.")
        return f"{PUBLIC_GATEWAY_BASE_URL}/api/v1"

    raw_host = (request.headers.get("host") or request.url.netloc or "").strip().lower()
    hostname = raw_host.split(":", 1)[0].rstrip(".")
    allowed_hosts = ALLOWED_GATEWAY_HOSTS | DEV_GATEWAY_HOSTS
    if hostname not in allowed_hosts:
        raise HTTPException(status_code=400, detail="Untrusted or unconfigured gateway host.")

    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    scheme = str(scheme).split(",", 1)[0].strip().lower()
    if scheme not in {"http", "https"}:
        scheme = "https"
    if scheme != "https" and hostname not in DEV_GATEWAY_HOSTS:
        scheme = "https"
    return f"{scheme}://{raw_host}/api/v1"
