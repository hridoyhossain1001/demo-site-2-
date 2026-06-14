import secrets

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.ad_campaign import AdCampaign
from app.models.ad_insight_daily import AdInsightDaily
from app.models.client import Client
from app.models.client_session import ClientSession
from app.models.client_support_note import ClientSupportNote
from app.models.client_user import ClientUser
from app.models.courier_booking_job import CourierBookingJob
from app.models.courier_order import CourierOrder
from app.models.event_dedup import EventDedup
from app.models.event_log import EventLog
from app.models.event_outbox import EventOutbox
from app.models.failed_event import FailedEvent
from app.models.incomplete_checkout import IncompleteCheckout
from app.models.notification_job import NotificationJob
from app.models.pending_event import PendingEvent
from app.models.plugin_connect_session import PluginConnectSession
from app.models.site_binding import SiteBinding
from app.models.trial_identity import TrialIdentity
from app.models.usage_counter import UsageCounter


KEY_TYPE_ALIASES = {
    "api": "api_key",
    "api_key": "api_key",
    "public": "public_key",
    "public_key": "public_key",
    "portal": "portal_key",
    "portal_key": "portal_key",
}


def normalize_client_key_type(key_type: str) -> str:
    normalized = KEY_TYPE_ALIASES.get(str(key_type or "").strip().lower())
    if not normalized:
        raise ValueError("Invalid key type")
    return normalized


def generate_client_secret(key_type: str) -> str:
    normalized = normalize_client_key_type(key_type)
    if normalized == "api_key":
        return secrets.token_urlsafe(32)
    if normalized in {"portal_key", "public_key"}:
        return secrets.token_urlsafe(24)
    raise ValueError("Invalid key type")


def rotate_client_secret(client: Client, key_type: str) -> tuple[str, str]:
    normalized = normalize_client_key_type(key_type)
    new_value = generate_client_secret(normalized)
    setattr(client, normalized, new_value)
    return normalized, new_value


async def delete_client_cascade(db: AsyncSession, client: Client) -> None:
    client_id = client.id
    await db.execute(sql_delete(CourierBookingJob).where(CourierBookingJob.client_id == client_id))
    await db.execute(sql_delete(CourierOrder).where(CourierOrder.client_id == client_id))
    await db.execute(sql_delete(AdCampaign).where(AdCampaign.ad_account_id.in_(select(AdAccount.id).where(AdAccount.client_id == client_id))))
    await db.execute(sql_delete(AdAccount).where(AdAccount.client_id == client_id))
    await db.execute(sql_delete(AdInsightDaily).where(AdInsightDaily.client_id == client_id))
    await db.execute(sql_delete(NotificationJob).where(NotificationJob.client_id == client_id))
    await db.execute(sql_delete(IncompleteCheckout).where(IncompleteCheckout.client_id == client_id))
    await db.execute(sql_delete(PluginConnectSession).where(PluginConnectSession.client_id == client_id))
    await db.execute(sql_delete(TrialIdentity).where(TrialIdentity.client_id == client_id))
    await db.execute(sql_delete(EventOutbox).where(EventOutbox.client_id == client_id))
    await db.execute(sql_delete(FailedEvent).where(FailedEvent.client_id == client_id))
    await db.execute(sql_delete(PendingEvent).where(PendingEvent.client_id == client_id))
    await db.execute(sql_delete(EventDedup).where(EventDedup.client_id == client_id))
    await db.execute(sql_delete(UsageCounter).where(UsageCounter.client_id == client_id))
    await db.execute(sql_delete(EventLog).where(EventLog.client_id == client_id))
    await db.execute(sql_delete(ClientSupportNote).where(ClientSupportNote.client_id == client_id))
    await db.execute(sql_delete(SiteBinding).where(SiteBinding.client_id == client_id))
    await db.execute(sql_delete(ClientSession).where(ClientSession.client_id == client_id))
    await db.execute(sql_delete(ClientUser).where(ClientUser.client_id == client_id))
    await db.delete(client)
