import httpx
import logging
from datetime import date
from app.security import decrypt_token

logger = logging.getLogger(__name__)

async def fetch_meta_daily_insights(
    access_token_enc: str,
    act_id: str,
    start_date: date,
    end_date: date
) -> list[dict]:
    token = decrypt_token(access_token_enc)
    clean_act_id = act_id.replace("act_", "")
    url = f"https://graph.facebook.com/v20.0/act_{clean_act_id}/insights"

    params = {
        "level": "campaign",
        "time_increment": 1,
        "time_range": f"{{\"since\":\"{start_date}\",\"until\":\"{end_date}\"}}",
        "fields": "campaign_id,campaign_name,spend,clicks,impressions,actions,action_values",
        "access_token": token,
        "limit": 500
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=30.0)
        if response.status_code == 400 and "request limit reached" in response.text.lower():
            logger.warning("Meta Graph API Rate Limit Hit. Activating backoff.")
            return []

        if response.status_code != 200:
            logger.error(f"Meta API error: {response.text}")
            response.raise_for_status()

        data = response.json().get("data", [])

        normalized = []
        for row in data:
            purchases = 0
            revenue = 0.0
            for action in row.get("actions", []):
                if action.get("action_type") == "purchase":
                    purchases = int(action.get("value", 0))
            for val in row.get("action_values", []):
                if val.get("action_type") == "purchase":
                    revenue = float(val.get("value", 0.0))

            normalized.append({
                "campaign_id": row.get("campaign_id"),
                "campaign_name": row.get("campaign_name"),
                "date": date.fromisoformat(row.get("date_start")),
                "spend": float(row.get("spend", 0.0)),
                "impressions": int(row.get("impressions", 0)),
                "clicks": int(row.get("clicks", 0)),
                "purchases": purchases,
                "revenue": revenue
            })
        return normalized
