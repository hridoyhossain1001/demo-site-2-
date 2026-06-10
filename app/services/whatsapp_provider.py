import httpx
import logging
import os
import re

logger = logging.getLogger(__name__)


class EvolutionWhatsAppProvider:
    """Provider for sending WhatsApp messages using Evolution API."""

    @staticmethod
    async def send_text(
        instance_name: str,
        to_number: str,
        message: str,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> dict:
        url = (base_url or os.getenv("EVOLUTION_API_BASE_URL", "")).strip()
        key = (api_key or os.getenv("EVOLUTION_API_KEY", "")).strip()

        if not url:
            raise ValueError("Evolution API base URL is not configured.")
        if not key:
            raise ValueError("Evolution API key is not configured.")

        # Ensure base URL format
        url = url.rstrip("/")
        endpoint = f"{url}/message/sendText/{instance_name}"

        # Normalize phone number to only digits
        cleaned_number = re.sub(r"\D", "", to_number)
        if not cleaned_number:
            raise ValueError(f"Invalid phone number format for WhatsApp destination: {to_number}")

        headers = {
            "apikey": key,
            "Content-Type": "application/json",
        }

        # Evolution API standard payload
        payload = {
            "number": cleaned_number,
            "text": message,
        }

        logger.info(f"Sending WhatsApp message to {cleaned_number} using Evolution instance '{instance_name}'...")

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, headers=headers, timeout=10.0)

        if response.status_code >= 400:
            logger.error(f"Evolution API returned error {response.status_code}: {response.text}")
            response.raise_for_status()

        return response.json()
