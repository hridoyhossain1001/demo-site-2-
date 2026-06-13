import hashlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CachedClient, get_current_client
from app.models.incomplete_checkout import IncompleteCheckout
from app.services.plan_service import require_growth_access


router = APIRouter()
RECENT_RECOVERY_SUPPRESSION_WINDOW = timedelta(minutes=30)


class IncompleteCheckoutUpsert(BaseModel):
    visitor_id: str = Field(min_length=8, max_length=255)
    phone: str = Field(min_length=8, max_length=32)
    customer_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=500)
    products: list[dict] = Field(default_factory=list, max_length=30)
    amount: Decimal = Field(default=Decimal("0"), ge=0, le=100000000)
    currency: str = Field(default="BDT", min_length=3, max_length=8)
    page_url: str | None = Field(default=None, max_length=1000)
    campaign_data: dict = Field(default_factory=dict)

    @field_validator("campaign_data", mode="before")
    @classmethod
    def normalize_empty_campaign_data(cls, value):
        # PHP json_encode serializes an empty associative array as [] unless
        # explicitly cast to object. Accept that legacy relay shape as empty.
        return {} if value == [] or value is None else value


class IncompleteCheckoutConvert(BaseModel):
    visitor_id: str | None = Field(default=None, max_length=255)
    phone: str = Field(min_length=8, max_length=32)
    order_id: str = Field(min_length=1, max_length=255)


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) == 11 and digits.startswith("01"):
        return "88" + digits
    if len(digits) == 10 and digits.startswith("1"):
        return "880" + digits
    if len(digits) == 13 and digits.startswith("8801"):
        return digits
    raise HTTPException(status_code=422, detail="A valid Bangladesh mobile number is required.")


def _phone_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def recover_open_checkouts_for_order(
    db: AsyncSession,
    *,
    client_id: int,
    phone: str,
    order_id: str,
    visitor_id: str | None = None,
    activity_window_start: datetime | None = None,
    activity_window_end: datetime | None = None,
) -> int:
    """Mark only real abandoned/contacted leads as recovered.

    Drafts still in "active" status mean the customer completed checkout before
    the 20-minute incomplete threshold, so they are hidden as ignored instead of
    appearing as recovered leads.
    """
    try:
        phone_hash = _phone_hash(_normalize_phone(phone))
    except HTTPException:
        return 0

    identity_filters = [IncompleteCheckout.phone_hash == phone_hash]
    clean_visitor_id = (visitor_id or "").strip()
    if len(clean_visitor_id) >= 8:
        identity_filters.append(IncompleteCheckout.visitor_id == clean_visitor_id)

    filters = [
        IncompleteCheckout.client_id == client_id,
        IncompleteCheckout.status.in_(["active", "incomplete", "contacted"]),
    ]
    if len(identity_filters) == 1:
        filters.append(identity_filters[0])
    else:
        filters.append(or_(*identity_filters))
    if activity_window_start is not None:
        filters.append(IncompleteCheckout.last_activity_at >= activity_window_start)
    if activity_window_end is not None:
        filters.append(IncompleteCheckout.last_activity_at <= activity_window_end)

    result = await db.execute(
        select(IncompleteCheckout)
        .where(*filters)
        .order_by(desc(IncompleteCheckout.last_activity_at), desc(IncompleteCheckout.id))
        .with_for_update()
    )
    drafts = result.scalars().all()
    if not drafts:
        return 0

    converted_at = datetime.now(timezone.utc)
    primary_draft = next((draft for draft in drafts if draft.status in {"incomplete", "contacted"}), None)
    if not primary_draft:
        for draft in drafts:
            draft.status = "ignored"
            draft.order_id = order_id.strip()
            draft.converted_at = converted_at
            draft.last_activity_at = converted_at
        return 0

    primary_draft.status = "recovered"
    primary_draft.order_id = order_id.strip()
    primary_draft.converted_at = converted_at
    primary_draft.last_activity_at = converted_at

    for draft in drafts:
        if draft.id == primary_draft.id:
            continue
        draft.status = "ignored"
        draft.order_id = order_id.strip()
        draft.converted_at = converted_at
        draft.last_activity_at = converted_at

    return 1


@router.post("/incomplete-checkouts/upsert")
async def upsert_incomplete_checkout(
    payload: IncompleteCheckoutUpsert,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    require_growth_access(client, "Incomplete checkout recovery")
    phone = _normalize_phone(payload.phone)
    phone_hash = _phone_hash(phone)
    visitor_id = payload.visitor_id.strip()
    result = await db.execute(
        select(IncompleteCheckout)
        .where(
            IncompleteCheckout.client_id == client.id,
            IncompleteCheckout.visitor_id == visitor_id,
            IncompleteCheckout.phone_hash == phone_hash,
            IncompleteCheckout.status.in_(["active", "incomplete", "contacted", "recovered", "ignored"]),
        )
        .order_by(desc(IncompleteCheckout.last_activity_at), desc(IncompleteCheckout.id))
        .limit(1)
    )
    draft = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if draft and draft.status in {"recovered", "ignored"}:
        converted_at = draft.converted_at
        if converted_at and converted_at.tzinfo is None:
            converted_at = converted_at.replace(tzinfo=timezone.utc)
        if converted_at and now - converted_at <= RECENT_RECOVERY_SUPPRESSION_WINDOW:
            return {"success": True, "id": draft.id, "status": draft.status, "suppressed": True}
        draft = None

    if not draft:
        draft = IncompleteCheckout(client_id=client.id, visitor_id=visitor_id, phone=phone, phone_hash=phone_hash)
        db.add(draft)

    draft.customer_name = (payload.customer_name or "").strip() or None
    draft.email = (payload.email or "").strip() or None
    draft.address = (payload.address or "").strip() or None
    draft.products = payload.products[:30]
    draft.amount = payload.amount
    draft.currency = payload.currency.strip().upper()
    draft.page_url = (payload.page_url or "").strip() or None
    draft.campaign_data = payload.campaign_data
    draft.last_activity_at = now
    if draft.status == "incomplete":
        draft.status = "active"

    stale_r = await db.execute(
        select(IncompleteCheckout)
        .where(
            IncompleteCheckout.client_id == client.id,
            IncompleteCheckout.visitor_id == visitor_id,
            IncompleteCheckout.phone_hash != phone_hash,
            IncompleteCheckout.status == "active",
            IncompleteCheckout.id != draft.id,
        )
        .with_for_update()
    )
    for stale_draft in stale_r.scalars().all():
        stale_draft.status = "ignored"
        stale_draft.converted_at = now
        stale_draft.last_activity_at = now

    await db.commit()
    await db.refresh(draft)
    return {"success": True, "id": draft.id, "status": draft.status}


@router.post("/incomplete-checkouts/convert")
async def convert_incomplete_checkout(
    payload: IncompleteCheckoutConvert,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    require_growth_access(client, "Incomplete checkout recovery")
    converted_count = await recover_open_checkouts_for_order(
        db,
        client_id=client.id,
        phone=payload.phone,
        order_id=payload.order_id,
        visitor_id=payload.visitor_id,
    )
    await db.commit()
    if not converted_count:
        return {"success": True, "converted": False}

    return {"success": True, "converted": True, "converted_count": converted_count}
