import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_client, CachedClient
from app.models.ad_account import AdAccount
from app.security import encrypt_token

logger = logging.getLogger(__name__)
router = APIRouter()


# â”€â”€â”€ Pydantic Schemas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class AdAccountCreate(BaseModel):
    platform: str = Field(..., description="Platform: 'meta' or 'tiktok'")
    external_account_id: str = Field(..., description="Act ID or Advertiser ID")
    account_name: Optional[str] = Field(None, description="Display name for the ad account")
    access_token: str = Field(..., description="Plaintext access token")
    refresh_token: Optional[str] = Field(None, description="Plaintext refresh token (for TikTok)")
    account_currency: str = Field("USD", description="Account currency (e.g. USD, BDT)")
    account_timezone: str = Field("UTC", description="Account timezone (e.g. UTC, Asia/Dhaka)")


class AdAccountResponse(BaseModel):
    id: int
    client_id: int
    platform: str
    external_account_id: str
    account_name: Optional[str] = None
    account_currency: str
    account_timezone: str
    is_active: bool
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True
    }


# â”€â”€â”€ Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/ad-accounts",
    response_model=AdAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a Meta or TikTok ad account"
)
async def connect_ad_account(
    body: AdAccountCreate,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    """
    Connects a new or existing Meta/TikTok ad account.
    Sensitive access and refresh tokens are encrypted using the encrypt_token helper.
    """
    platform_clean = body.platform.strip().lower()
    if platform_clean not in ("meta", "tiktok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Platform must be 'meta' or 'tiktok'"
        )

    # Check if the ad account already exists for this client
    query = select(AdAccount).where(
        AdAccount.client_id == client.id,
        AdAccount.platform == platform_clean,
        AdAccount.external_account_id == body.external_account_id
    )
    result = await db.execute(query)
    ad_account = result.scalar_one_or_none()

    # Encrypt tokens
    access_token_enc = encrypt_token(body.access_token)
    refresh_token_enc = encrypt_token(body.refresh_token) if body.refresh_token else None

    if ad_account:
        # Update existing
        ad_account.account_name = body.account_name or ad_account.account_name
        ad_account.access_token_enc = access_token_enc
        if refresh_token_enc:
            ad_account.refresh_token_enc = refresh_token_enc
        ad_account.account_currency = body.account_currency
        ad_account.account_timezone = body.account_timezone
        ad_account.is_active = True
        logger.info(f"Updated connection for ad account {platform_clean}:{body.external_account_id} for client {client.id}")
    else:
        # Create new
        ad_account = AdAccount(
            client_id=client.id,
            platform=platform_clean,
            external_account_id=body.external_account_id,
            account_name=body.account_name,
            access_token_enc=access_token_enc,
            refresh_token_enc=refresh_token_enc,
            account_currency=body.account_currency,
            account_timezone=body.account_timezone,
            is_active=True
        )
        db.add(ad_account)
        logger.info(f"Connected new ad account {platform_clean}:{body.external_account_id} for client {client.id}")

    try:
        await db.commit()
        await db.refresh(ad_account)
    except Exception as exc:
        await db.rollback()
        logger.error(f"Error saving ad account connection: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while saving ad account connection."
        )

    return ad_account


@router.get(
    "/ad-accounts",
    response_model=List[AdAccountResponse],
    summary="List connected ad accounts"
)
async def list_ad_accounts(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists all connected ad accounts for the authenticated client.
    Does not return encrypted or decrypted tokens.
    """
    query = select(AdAccount).where(
        AdAccount.client_id == client.id
    ).order_by(AdAccount.id.desc())

    result = await db.execute(query)
    ad_accounts = result.scalars().all()
    return ad_accounts


@router.delete(
    "/ad-accounts/{id}",
    summary="Disconnect/delete an ad account"
)
async def disconnect_ad_account(
    id: int,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    """
    Disconnects and deletes the specified ad account from the system.
    """
    query = select(AdAccount).where(
        AdAccount.client_id == client.id,
        AdAccount.id == id
    )
    result = await db.execute(query)
    ad_account = result.scalar_one_or_none()

    if not ad_account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ad account not found."
        )

    try:
        await db.delete(ad_account)
        await db.commit()
        logger.info(f"Disconnected ad account id={id} for client {client.id}")
    except Exception as exc:
        await db.rollback()
        logger.error(f"Error deleting ad account id={id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while deleting ad account."
        )

    return {"status": "success", "message": "Ad account disconnected successfully."}
