import hashlib
import os
import uuid
from typing import Any

from app.schemas.event import CustomData, EventData, UserData


COMMERCE_EVENTS = {"ViewContent", "AddToCart", "ViewCart", "RemoveFromCart", "InitiateCheckout", "AddPaymentInfo", "Purchase"}
EVENT_ALIASES = {
    "page_view": "PageView",
    "view_item": "ViewContent",
    "add_to_cart": "AddToCart",
    "view_cart": "ViewCart",
    "remove_from_cart": "RemoveFromCart",
    "begin_checkout": "InitiateCheckout",
    "add_payment_info": "AddPaymentInfo",
    "purchase": "Purchase",
    "lead": "Lead",
    "search": "Search",
}
DEFAULT_CURRENCY = os.getenv("DEFAULT_EVENT_CURRENCY", "BDT").upper()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean_ids(values: Any) -> list[str]:
    seen = set()
    cleaned = []
    for value in _as_list(values):
        if value is None:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _contents_from_ids(content_ids: list[str], content_type: str) -> list[dict]:
    return [{"content_id": cid, "content_type": content_type} for cid in content_ids]


def _ids_from_contents(contents: Any) -> list[str]:
    ids = []
    for item in _as_list(contents):
        if not isinstance(item, dict):
            continue
        content_id = item.get("content_id") or item.get("id") or item.get("item_id")
        if content_id:
            ids.append(content_id)
    return _clean_ids(ids)


def _numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quantity(value: Any) -> int:
    try:
        return max(int(float(value)), 1)
    except (TypeError, ValueError):
        return 1


def _stable_event_suffix(event: EventData) -> str:
    custom_data = event.custom_data.model_dump(exclude_none=True) if event.custom_data else {}
    user_data = event.user_data.model_dump(exclude_none=True) if event.user_data else {}
    external_ids = user_data.get("external_id") or []
    identity = str(external_ids[0]) if external_ids else ""
    basis = "|".join(
        [
            str(event.event_name or "").lower(),
            ",".join(custom_data.get("content_ids") or []),
            str(custom_data.get("value") or ""),
            event.event_source_url or "",
            identity,
        ]
    )
    digest = hashlib.md5(basis.encode("utf-8")).hexdigest()[:20]
    return digest


def calculate_emq_score(event: EventData, *, customer_provided_only: bool = True) -> float:
    """
    Calculate a realistic Event Match Quality (EMQ) score from 0.0 to 10.0
    based on official Meta (Facebook) CAPI and TikTok Events API guidelines.
    """
    if not event.user_data:
        return 0.0

    ud = event.user_data
    score = 0.0

    # Helper function to check if a list field has non-empty elements
    def has_list_val(val) -> bool:
        if not val:
            return False
        return any(bool(x and str(x).strip()) for x in val)

    # Helper function to check if a string field is non-empty
    def has_str_val(val) -> bool:
        return bool(val and str(val).strip())

    # Meta CAPI/TikTok high-value user identifiers
    # 1. Email (em) - 2.5 points
    if has_list_val(ud.em):
        score += 2.5

    # 2. Phone (ph) - 2.5 points
    if has_list_val(ud.ph):
        score += 2.5

    # 3. Browser ID / First-party Cookie (fbp / ttp) - 1.0 point
    if has_str_val(ud.fbp) or has_str_val(ud.ttp):
        score += 1.0

    # 4. Click ID (fbc / ttclid) - 1.0 point
    if has_str_val(ud.fbc) or has_str_val(ud.ttclid):
        score += 1.0

    # 5. External ID - 1.0 point (Crucial matching parameter)
    if has_list_val(ud.external_id):
        score += 1.0

    if not customer_provided_only:
        # 6. IP Address - 0.5 point
        if has_str_val(ud.client_ip_address):
            score += 0.5

        # 7. User Agent - 0.5 point
        if has_str_val(ud.client_user_agent):
            score += 0.5

    # 8. Individual PII parameters - 0.3 to 0.4 points each
    if has_list_val(ud.fn):
        score += 0.4
    if has_list_val(ud.ln):
        score += 0.4
    if has_list_val(ud.ct):
        score += 0.3
    if has_list_val(ud.st):
        score += 0.3
    if has_list_val(ud.zp):
        score += 0.3
    if has_list_val(ud.country):
        score += 0.3

    return min(10.0, round(score, 1))



def boost_event_quality(
    event: EventData,
    *,
    cookies: dict[str, str] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> EventData:
    """Normalize and enrich an event before it is saved/sent to ad platforms."""
    cookies = cookies or {}
    event.event_name = EVENT_ALIASES.get(event.event_name, event.event_name)

    if not event.user_data:
        event.user_data = UserData()
    if ip_address and not event.user_data.client_ip_address:
        event.user_data.client_ip_address = ip_address
    if user_agent and not event.user_data.client_user_agent:
        event.user_data.client_user_agent = user_agent

    if not event.user_data.fbp and cookies.get("_fbp"):
        event.user_data.fbp = cookies["_fbp"]
    if not event.user_data.fbc and cookies.get("_fbc"):
        event.user_data.fbc = cookies["_fbc"]
    if not event.user_data.ttp and cookies.get("_ttp"):
        event.user_data.ttp = cookies["_ttp"]
    if not event.user_data.ttclid and cookies.get("_ttclid"):
        event.user_data.ttclid = cookies["_ttclid"]

    if not event.event_id:
        event.event_id = f"bk_{str(event.event_name or '').lower()}_{_stable_event_suffix(event)}"

    if event.event_name in COMMERCE_EVENTS:
        if not event.custom_data:
            event.custom_data = CustomData()
        cd = event.custom_data
        cd.content_type = cd.content_type or "product"

        contents = cd.contents or []
        content_ids = _clean_ids(cd.content_ids) or _ids_from_contents(contents)
        if content_ids:
            cd.content_ids = content_ids
        if content_ids and not contents:
            cd.contents = _contents_from_ids(content_ids, cd.content_type)

        numeric_value = _numeric(cd.value)
        if numeric_value is not None:
            cd.value = numeric_value
        if numeric_value is not None and not cd.currency:
            cd.currency = DEFAULT_CURRENCY
        if cd.currency:
            cd.currency = str(cd.currency).upper()

        if cd.num_items is None:
            if contents:
                cd.num_items = sum(_quantity(item.get("quantity")) for item in contents if isinstance(item, dict))
            elif content_ids:
                cd.num_items = len(content_ids)

        if event.event_name == "Purchase" and not cd.order_id:
            for key in ("transaction_id", "order_number", "orderId"):
                value = getattr(cd, key, None)
                if value is None and getattr(cd, "model_extra", None) is not None:
                    value = cd.model_extra.get(key)
                if value:
                    cd.order_id = str(value)
                    break

    # ─── Campaign Attribution & UTM Normalization ───────────────────────
    if event.custom_data:
        cd = event.custom_data
        ud = event.user_data

        def get_extra(model, key):
            val = getattr(model, key, None)
            if val is not None:
                return val
            extra = getattr(model, "model_extra", None) or {}
            return extra.get(key)

        def set_extra(model, key, val):
            try:
                setattr(model, key, val)
                return
            except Exception:
                pass
            if hasattr(model, "model_extra"):
                if getattr(model, "model_extra", None) is None:
                    try:
                        object.__setattr__(model, "model_extra", {})
                    except Exception:
                        try:
                            model.model_extra = {}
                        except Exception:
                            pass
                if isinstance(model.model_extra, dict):
                    model.model_extra[key] = val
                    return
            if hasattr(model, "__dict__"):
                model.__dict__[key] = val

        # 1. Click ID based campaign fallback mapping
        if not get_extra(cd, "utm_source") and ud:
            if getattr(ud, "ttclid", None):
                set_extra(cd, "utm_source", "tiktok")
                set_extra(cd, "campaign_source", "tiktok")
                if not get_extra(cd, "utm_medium"):
                    set_extra(cd, "utm_medium", "paid_social")
            elif getattr(ud, "fbc", None):
                set_extra(cd, "utm_source", "facebook")
                set_extra(cd, "campaign_source", "facebook")
                if not get_extra(cd, "utm_medium"):
                    set_extra(cd, "utm_medium", "paid_social")

        # 2. Campaign parameter normalizations
        for field in ("utm_source", "campaign_source", "utm_medium"):
            val = get_extra(cd, field)
            if val:
                set_extra(cd, field, str(val).strip().lower())

        utm_camp = get_extra(cd, "utm_campaign")
        if utm_camp:
            # Slugify: lowercase and replace spaces/hyphens with underscores
            slugified = str(utm_camp).strip().lower().replace(" ", "_").replace("-", "_")
            set_extra(cd, "utm_campaign", slugified)

    if not event.custom_data:
        event.custom_data = CustomData()
    try:
        setattr(event.custom_data, "_enriched", True)
    except Exception:
        if isinstance(getattr(event.custom_data, "model_extra", None), dict):
            event.custom_data.model_extra["_enriched"] = True

    # Automatically calculate and set the EMQ score
    event.emq_score = calculate_emq_score(event, customer_provided_only=True)

    return event


def event_signal_flags(event_data: dict) -> dict[str, bool]:
    custom_data = event_data.get("custom_data") or {}
    user_data = event_data.get("user_data") or {}
    content_ids = custom_data.get("content_ids") or []
    contents = custom_data.get("contents") or []
    has_email_phone = bool(user_data.get("em") or user_data.get("ph"))
    has_browser = bool(user_data.get("fbp") or user_data.get("fbc") or user_data.get("ttp") or user_data.get("ttclid"))
    return {
        "has_content_ids": bool(content_ids),
        "has_contents": bool(contents),
        "has_value": custom_data.get("value") is not None,
        "has_currency": bool(custom_data.get("currency")),
        "has_user_match": bool(has_email_phone or has_browser or (user_data.get("client_ip_address") and user_data.get("client_user_agent"))),
        "has_email_phone": has_email_phone,
        "has_click_id": bool(user_data.get("fbc") or user_data.get("ttclid")),
        "has_event_id": bool(event_data.get("event_id")),
        "has_utm": bool(custom_data.get("utm_source") or custom_data.get("utm_campaign")),
    }
