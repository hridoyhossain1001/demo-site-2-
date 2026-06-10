"""
Shared EventLog helper — builds kwargs dict for creating EventLog rows.
Used by events.py router and event_worker.py service to avoid duplication.
"""

import hashlib

from app.services.event_quality import event_signal_flags
from app.services.visitor_context import (
    extract_device_metadata,
    extract_event_custom_data,
    geo_context_from_ip,
)

PLACEHOLDER_IPS = {"", "0.0.0.0", "8.8.8.8", "127.0.0.1", None}


def _visitor_ip_from_event(event_data: dict, fallback_ip: str | None) -> str | None:
    user_data = event_data.get("user_data") or {}
    if not isinstance(user_data, dict):
        return fallback_ip
    visitor_ip = user_data.get("client_ip_address")
    if visitor_ip not in PLACEHOLDER_IPS:
        return visitor_ip
    return fallback_ip


def _first_scalar(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if value is None:
        return ""
    return str(value).strip()


def _user_agent_from_event(event_data: dict, fallback_user_agent: str | None) -> str | None:
    if fallback_user_agent:
        return fallback_user_agent
    user_data = event_data.get("user_data") or {}
    if not isinstance(user_data, dict):
        return None
    return _first_scalar(user_data.get("client_user_agent")) or None


def _visitor_key_from_event(event_data: dict, visitor_ip: str | None, user_agent: str | None) -> str | None:
    user_data = event_data.get("user_data") or {}
    if not isinstance(user_data, dict):
        user_data = {}

    for label, value in (
        ("external", _first_scalar(user_data.get("external_id"))),
        ("fbp", _first_scalar(user_data.get("fbp"))),
        ("ttp", _first_scalar(user_data.get("ttp"))),
        ("fbc", _first_scalar(user_data.get("fbc"))),
        ("ttclid", _first_scalar(user_data.get("ttclid"))),
    ):
        if value:
            digest = hashlib.sha256(f"{label}:{value}".encode("utf-8")).hexdigest()
            return f"{label}:{digest[:48]}"

    if visitor_ip and user_agent:
        digest = hashlib.sha256(f"network:{visitor_ip}|{user_agent}".encode("utf-8")).hexdigest()
        return f"net:{digest[:48]}"
    return None


def build_event_log_kwargs(
    client_id: int,
    event_data,
    status: str,
    ip_address: str | None,
    user_agent: str | None = None,
    device_metadata: dict | None = None,
    **extra,
) -> dict:
    """
    EventLog row তৈরির জন্য kwargs dict build করে।
    event_data একটি dict (model_dump output) বা Pydantic model হতে পারে।
    """
    # Enforce Pydantic model check and convert to dict
    if hasattr(event_data, "model_dump"):
        event_data = event_data.model_dump()
    elif hasattr(event_data, "dict"):
        event_data = event_data.dict()

    if not isinstance(event_data, dict):
        event_data = {}

    custom_data = extract_event_custom_data(event_data)
    utm_source = custom_data.get("utm_source")
    resolved_user_agent = _user_agent_from_event(event_data, user_agent)
    visitor_ip = _visitor_ip_from_event(event_data, ip_address)
    visitor_key = _visitor_key_from_event(event_data, visitor_ip, resolved_user_agent)
    geo_context = geo_context_from_ip(visitor_ip)
    device_context = extract_device_metadata(
        custom_data,
        user_agent=resolved_user_agent,
        context_device=device_metadata,
    )

    # Fix the boolean float casting bug that converts True to 1.0 / False to 0.0
    val = custom_data.get("value")
    if isinstance(val, bool):
        value = None
    else:
        try:
            value = float(val) if val is not None else None
        except (TypeError, ValueError):
            value = None

    campaign_source = custom_data.get("campaign_source") or utm_source
    ad_platform = custom_data.get("ad_platform") or custom_data.get("bk_platform")
    ad_campaign_id = custom_data.get("ad_campaign_id") or custom_data.get("bk_campaign_id")
    ad_adset_id = custom_data.get("ad_adset_id") or custom_data.get("bk_adset_id")
    ad_id = custom_data.get("ad_id") or custom_data.get("bk_ad_id")

    return {
        "client_id": client_id,
        "event_name": event_data.get("event_name") or "unknown",
        "event_id": event_data.get("event_id"),
        "event_count": 1,
        "status": status,
        "ip_address": visitor_ip,
        "visitor_key": visitor_key,
        **geo_context,
        **device_context,
        "emq_score": event_data.get("emq_score"),
        "value": value,
        "currency": custom_data.get("currency"),
        "campaign_source": campaign_source,
        "utm_source": utm_source,
        "utm_medium": custom_data.get("utm_medium"),
        "utm_campaign": custom_data.get("utm_campaign"),
        "utm_content": custom_data.get("utm_content"),
        "utm_term": custom_data.get("utm_term"),
        "ad_platform": ad_platform,
        "ad_campaign_id": ad_campaign_id,
        "ad_adset_id": ad_adset_id,
        "ad_id": ad_id,
        **event_signal_flags(event_data),
        **extra,
    }
