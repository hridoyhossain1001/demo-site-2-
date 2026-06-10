import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from app.models.client import Client
from app.models.client_user import ClientUser
from app.models.whatsapp_instance import WhatsAppInstance
from app.routers.client_api import update_profile, get_profile, ProfileUpdateRequest

@pytest.mark.asyncio
async def test_update_profile_saves_whatsapp_settings_and_links_instance():
    # 1. Setup mock models
    client = Client(
        id=1,
        name="Test Store",
        owner_notify_whatsapp=False,
        owner_whatsapp_number=None,
        whatsapp_instance_id=None,
        plan_tier="growth"
    )
    user = ClientUser(id=1, email="owner@store.com", notification_email=None)

    # 2. Setup mock DB Session
    db = AsyncMock(spec=AsyncSession)
    
    # Active WhatsAppInstance that should be linked when notifications are enabled
    active_instance = WhatsAppInstance(id=42, instance_name="whatsapp-active", status="active")
    
    # Mock database execute result for WhatsAppInstance select
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = active_instance
    db.execute.return_value = mock_result

    # 3. Setup mock Request and cookie session extractor
    request = MagicMock(spec=Request)
    
    # Mock get_client_user_from_cookie to return the mock user
    async def mock_get_user(req, session):
        return user, None, None

    import app.routers.client_api as client_api
    original_get_user = client_api.get_client_user_from_cookie
    client_api.get_client_user_from_cookie = mock_get_user

    try:
        # 4. Trigger profile update with ownerNotifyWhatsapp=True and ownerWhatsappNumber
        payload = ProfileUpdateRequest(
            name="Updated Name",
            ownerNotifyWhatsapp=True,
            ownerWhatsappNumber="8801700000000"
        )
        
        response = await update_profile(
            request=request,
            payload=payload,
            client=client,
            db=db
        )

        # 5. Assertions
        assert client.name == "Updated Name"
        assert client.owner_notify_whatsapp is True
        assert client.owner_whatsapp_number == "8801700000000"
        assert client.whatsapp_instance_id == 42  # Linked automatically
        assert response["success"] is True
        assert response["profile"]["ownerNotifyWhatsapp"] is True
        assert response["profile"]["ownerWhatsappNumber"] == "8801700000000"
        db.commit.assert_called_once()

        # 6. Trigger profile update with ownerNotifyWhatsapp=False (disable alerts)
        payload_disabled = ProfileUpdateRequest(
            name="Updated Name",
            ownerNotifyWhatsapp=False,
            ownerWhatsappNumber=""
        )
        
        response_disabled = await update_profile(
            request=request,
            payload=payload_disabled,
            client=client,
            db=db
        )
        
        assert client.owner_notify_whatsapp is False
        assert client.owner_whatsapp_number is None
        assert client.whatsapp_instance_id is None  # Unlinked automatically
        assert response_disabled["profile"]["ownerNotifyWhatsapp"] is False

    finally:
        # Restore mock helper
        client_api.get_client_user_from_cookie = original_get_user
