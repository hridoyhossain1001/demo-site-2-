import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, model_validator

from app.services.identity import hash_pii, normalize_bd_phone


def _clean_and_hash(val: Any, field: str) -> str:
    """Normalize and hash PII data according to Meta CAPI rules."""
    if val is None:
        return ""
    if not isinstance(val, str):
        val = str(val)
    if not val.strip():
        return ""

    val = val.strip().lower()
    if re.match(r"^[a-f0-9]{64}$", val):
        return val

    if field == "ph":
        val = normalize_bd_phone(val)
    elif field in ("fn", "ln", "ct"):
        val = re.sub(r"[^\w\s]", "", val)

    return hash_pii(val)


class UserData(BaseModel):
    """Facebook CAPI user data."""
    em: Optional[List[str]] = None
    ph: Optional[List[str]] = None
    fn: Optional[List[str]] = None
    ln: Optional[List[str]] = None
    ct: Optional[List[str]] = None
    st: Optional[List[str]] = None
    zp: Optional[List[str]] = None
    country: Optional[List[str]] = None
    external_id: Optional[List[str]] = None
    client_ip_address: Optional[str] = None
    client_user_agent: Optional[str] = None
    fbc: Optional[str] = None
    fbp: Optional[str] = None
    ttp: Optional[str] = None
    ttclid: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def auto_hash_pii(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        hashable_fields = ["em", "ph", "fn", "ln", "ct", "st", "zp", "country"]
        for field in hashable_fields:
            if field in data and data[field] is not None:
                val = data[field]
                if isinstance(val, (str, int, float)):
                    val = [str(val)]

                if isinstance(val, list):
                    cleaned_list = []
                    for item in val:
                        if isinstance(item, (str, int, float)):
                            cleaned_list.append(_clean_and_hash(item, field))
                        else:
                            cleaned_list.append(item)
                    data[field] = cleaned_list

        return data


class CustomData(BaseModel):
    """Custom data for commerce and conversion events."""
    model_config = {"extra": "allow"}
    value: Optional[float] = None
    currency: Optional[str] = None
    content_ids: Optional[List[str]] = None
    content_type: Optional[str] = None
    order_id: Optional[str] = None
    num_items: Optional[int] = None
    contents: Optional[List[Dict[str, Any]]] = None


class EventData(BaseModel):
    """A normalized event payload."""
    event_name: str
    event_time: int
    action_source: str = "website"
    event_id: Optional[str] = None
    event_source_url: Optional[str] = None
    user_data: Optional[UserData] = None
    custom_data: Optional[CustomData] = None
    raw_order_data: Optional[Dict[str, Any]] = None
    emq_score: Optional[float] = None


class EventsPayload(BaseModel):
    """Incoming batched events payload."""
    data: List[EventData]


class EventsResponse(BaseModel):
    status: str
    events_received: int
    message: str
