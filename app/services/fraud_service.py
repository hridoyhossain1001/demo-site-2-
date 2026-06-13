import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pending_event import PendingEvent
from app.schemas.event import EventData, UserData, _clean_and_hash
from app.services.geoip_service import get_location_data

logger = logging.getLogger(__name__)

FRAUD_AUTO_HOLD_THRESHOLD = int(os.getenv("FRAUD_AUTO_HOLD_THRESHOLD", "90"))

DISPOSABLE_DOMAINS = {
    "tempmail.com", "temp-mail.org", "10minutemail.com", "yopmail.com",
    "mailinator.com", "dispostable.com", "guerrillamail.com", "sharklasers.com",
    "getairmail.com", "maildrop.cc", "throwawaymail.com", "tempmailaddress.com",
    "burnermail.io", "tempmail.net", "fakeinbox.com", "crazymailing.com",
    "mailnesia.com", "mailcatch.com", "trashmail.com", "tempail.com",
}

GIBBERISH_PATTERNS = [
    r"asdfgh", r"qwerty", r"zxcvbn", r"12345", r"qwer", r"asdf", r"zxcv",
    r"uiop", r"hjkl", r"bnm",
]
_GIBBERISH_REGEX = re.compile("|".join(GIBBERISH_PATTERNS), re.IGNORECASE)


def is_disposable_email(email_domain: str) -> bool:
    if not email_domain:
        return False
    return email_domain.strip().lower() in DISPOSABLE_DOMAINS


def is_gibberish(name: str) -> bool:
    if not name:
        return False
    name_clean = name.strip()
    if len(name_clean) < 3:
        return True
    if name_clean.isdigit():
        return True
    if re.search(r"(.)\1{3,}", name_clean.lower()):
        return True
    if _GIBBERISH_REGEX.search(name_clean):
        return True
    if len(name_clean) > 4 and name_clean.isascii() and name_clean.isalpha():
        vowels = set("aeiouy")
        if not any(char in vowels for char in name_clean.lower()):
            return True
    return False


def should_auto_hold_for_fraud(score: int | None) -> bool:
    """Return True when product policy requires manual review before sending Purchase."""
    if not FRAUD_AUTO_HOLD_THRESHOLD or FRAUD_AUTO_HOLD_THRESHOLD < 1:
        return False
    return (score or 0) >= FRAUD_AUTO_HOLD_THRESHOLD


def check_ip_location_mismatch(client_ip: str, user_data: UserData) -> bool:
    if not client_ip or not user_data or not user_data.country:
        return False

    loc = get_location_data(client_ip)
    loc_country = loc.get("country")
    if not loc_country:
        return False

    hashed_loc_country = _clean_and_hash(loc_country, "country")
    return hashed_loc_country not in user_data.country


def _event_data_from_row(row: Any) -> dict:
    if isinstance(row, dict):
        return row
    return getattr(row, "event_data", None) or {}


async def check_velocity(
    db: AsyncSession,
    client_id: int,
    client_ip: str,
    phone_hashes: List[str],
) -> bool:
    """Return True when the incoming order reaches IP or phone velocity limits."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    base_filters = (
        PendingEvent.client_id == client_id,
        PendingEvent.status == "pending",
        PendingEvent.created_at >= cutoff,
    )

    ip_count = 1 if client_ip else 0
    phone_count = 1 if phone_hashes else 0
    phone_hash_set = set(phone_hashes or [])

    ip_count_from_sql = False
    if client_ip:
        try:
            result = await db.execute(
                select(func.count(PendingEvent.id)).where(
                    and_(
                        *base_filters,
                        PendingEvent.event_data["user_data"]["client_ip_address"].as_string() == client_ip,
                    )
                )
            )
            ip_count += int(result.scalar() or 0)
            ip_count_from_sql = True
        except Exception as exc:
            logger.warning(f"Velocity SQL IP count failed; falling back to row scan: {exc}")

    result = await db.execute(select(PendingEvent.event_data).where(and_(*base_filters)))
    pending_event_data = result.scalars().all()

    for row in pending_event_data:
        event_data = _event_data_from_row(row)
        ud = event_data.get("user_data", {}) or {}

        if not ip_count_from_sql:
            pe_ip = ud.get("client_ip_address")
            if pe_ip and client_ip and pe_ip == client_ip:
                ip_count += 1

        pe_ph = ud.get("ph") or []
        if isinstance(pe_ph, list):
            for ph in pe_ph:
                if ph in phone_hash_set:
                    phone_count += 1
                    break

    return (ip_count >= 3) or (phone_count >= 3)


async def calculate_fraud_score(
    db: AsyncSession,
    client_id: int,
    event: EventData,
    client_ip: str,
) -> Tuple[int, Dict[str, bool]]:
    score = 0
    details = {
        "ip_mismatch": False,
        "disposable_email": False,
        "velocity_limit": False,
        "gibberish_name": False,
    }

    if not event or not event.user_data:
        return score, details

    user_data = event.user_data
    custom_data = event.custom_data or getattr(event, "custom_data", None)
    custom_dict = (
        custom_data.model_dump(exclude_none=True)
        if hasattr(custom_data, "model_dump")
        else (custom_data or {})
    )
    if getattr(custom_data, "model_extra", None):
        custom_dict.update(custom_data.model_extra)

    if check_ip_location_mismatch(client_ip, user_data):
        score += 25
        details["ip_mismatch"] = True

    email_domain = custom_dict.get("email_domain") or custom_dict.get("billing_email_domain")
    if email_domain and is_disposable_email(email_domain):
        score += 30
        details["disposable_email"] = True

    phone_hashes = user_data.ph or []
    if await check_velocity(db, client_id, client_ip, phone_hashes):
        score += 35
        details["velocity_limit"] = True

    raw_first_name = custom_dict.get("raw_first_name") or custom_dict.get("billing_first_name_raw")
    if raw_first_name and is_gibberish(raw_first_name):
        score += 20
        details["gibberish_name"] = True

    score = min(score, 100)
    logger.info(f"[Client #{client_id}] Fraud engine completed. Score: {score}/100. Details: {details}")
    return score, details
