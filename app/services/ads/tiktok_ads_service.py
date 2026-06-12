import httpx
import logging
from datetime import date
from app.security import decrypt_token

logger = logging.getLogger(__name__)

async def fetch_tiktok_daily_insights(
    access_token_enc: str,
    advertiser_id: str,
    start_date: date,
    end_date: date
) -> list[dict]:
    token = decrypt_token(access_token_enc)
    url = "https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/"

    headers = {
        "Access-Token": token,
        "Content-Type": "application/json"
    }

    page_size = 100
    params = {
        "advertiser_id": advertiser_id,
        "report_type": "BASIC",
        "data_level": "AUCTION_CAMPAIGN",
        "dimensions": '["campaign_id", "stat_time_day"]',
        "metrics": '["spend", "clicks", "impressions", "conversion", "conversion_value"]',
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "page_size": page_size,
    }

    async with httpx.AsyncClient() as client:
        rows = []
        page = 1
        while True:
            page_params = {**params, "page": page}
            response = await client.get(url, headers=headers, params=page_params, timeout=30.0)
            if response.status_code != 200:
                logger.error(f"TikTok API error: {response.text}")
                response.raise_for_status()

            res_data = response.json()
            if res_data.get("code") != 0:
                logger.error(f"TikTok Business API Business Error: {res_data.get('message')}")
                break

            data = res_data.get("data", {}) or {}
            page_rows = data.get("list", []) or []
            rows.extend(page_rows)
            page_info = data.get("page_info", {}) or {}
            total_page = int(page_info.get("total_page") or 0)
            if total_page:
                if page >= total_page:
                    break
            elif len(page_rows) < page_size:
                break
            page += 1

        normalized = []
        for row in rows:
            metrics = row.get("metrics", {})
            dimensions = row.get("dimensions", {})

            # Dimensions
            campaign_id = dimensions.get("campaign_id")
            # Get campaign name from metadata or dimensions if returned
            campaign_name = dimensions.get("campaign_name") or f"Campaign {campaign_id}"
            stat_date = date.fromisoformat(dimensions.get("stat_time_day").split()[0])

            normalized.append({
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "date": stat_date,
                "spend": float(metrics.get("spend", 0.0)),
                "impressions": int(metrics.get("impressions", 0)),
                "clicks": int(metrics.get("clicks", 0)),
                "purchases": int(metrics.get("conversion", 0)),
                "revenue": float(metrics.get("conversion_value", 0.0))
            })
        return normalized
