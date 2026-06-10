import asyncio
import logging
import os
from datetime import datetime, date, timedelta, timezone
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.ad_account import AdAccount
from app.models.ad_campaign import AdCampaign
from app.models.ad_insight_daily import AdInsightDaily
from app.services.ads.meta_ads_service import fetch_meta_daily_insights
from app.services.ads.tiktok_ads_service import fetch_tiktok_daily_insights

logger = logging.getLogger(__name__)

async def sync_ad_account_insights(db, account: AdAccount):
    today = date.today()
    start_date = today - timedelta(days=7)  # Re-fetch 7 days

    logger.info(f"Syncing ad account {account.platform}:{account.external_account_id}")

    insights = []
    try:
        if account.platform == "meta":
            insights = await fetch_meta_daily_insights(
                account.access_token_enc,
                account.external_account_id,
                start_date,
                today
            )
        elif account.platform == "tiktok":
            insights = await fetch_tiktok_daily_insights(
                account.access_token_enc,
                account.external_account_id,
                start_date,
                today
            )

        for row in insights:
            # 1. Ensure campaign exists in cache
            campaign_res = await db.execute(
                select(AdCampaign).where(
                    AdCampaign.ad_account_id == account.id,
                    AdCampaign.external_campaign_id == row["campaign_id"]
                )
            )
            campaign = campaign_res.scalar_one_or_none()
            if not campaign:
                campaign = AdCampaign(
                    ad_account_id=account.id,
                    platform=account.platform,
                    external_campaign_id=row["campaign_id"],
                    name=row["campaign_name"],
                    status="ACTIVE"
                )
                db.add(campaign)
                await db.flush()
            elif campaign.name != row["campaign_name"]:
                campaign.name = row["campaign_name"]

            # 2. Upsert daily insight metrics
            insight_res = await db.execute(
                select(AdInsightDaily).where(
                    AdInsightDaily.client_id == account.client_id,
                    AdInsightDaily.platform == account.platform,
                    AdInsightDaily.external_campaign_id == row["campaign_id"],
                    AdInsightDaily.insight_date == row["date"]
                )
            )
            insight = insight_res.scalar_one_or_none()
            if not insight:
                insight = AdInsightDaily(
                    client_id=account.client_id,
                    platform=account.platform,
                    external_campaign_id=row["campaign_id"],
                    insight_date=row["date"]
                )
                db.add(insight)

            insight.spend = row["spend"]
            insight.impressions = row["impressions"]
            insight.clicks = row["clicks"]
            insight.platform_purchases = row["purchases"]
            insight.platform_revenue = row["revenue"]
            insight.currency = account.account_currency

        account.last_synced_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error(f"Failed sync for ad account {account.id}: {exc}", exc_info=True)

async def run_ad_sync_forever():
    logger.info("Starting ad sync worker daemon...")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(AdAccount).where(AdAccount.is_active == True))
                active_accounts = result.scalars().all()

            for account in active_accounts:
                async with AsyncSessionLocal() as db:
                    fresh_account = await db.get(AdAccount, account.id)
                    if fresh_account:
                        await sync_ad_account_insights(db, fresh_account)
                        # Avoid aggressive API hitting / respect rate limits
                        await asyncio.sleep(5)

            # Run every 6 hours by default, customizable via environment variable
            interval = int(os.getenv("AD_SYNC_INTERVAL_SECONDS", "21600"))
            await asyncio.sleep(interval)
        except Exception as exc:
            logger.error(f"Error in ad sync loop: {exc}")
            await asyncio.sleep(60)
