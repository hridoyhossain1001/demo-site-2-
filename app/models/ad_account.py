from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base

class AdAccount(Base):
    __tablename__ = "ad_accounts"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(30), nullable=False)                    # 'meta' or 'tiktok'
    external_account_id = Column(String(100), nullable=False)        # Act ID or Advertiser ID
    account_name = Column(String(200), nullable=True)
    access_token_enc = Column(Text, nullable=False)                  # Fernet encrypted token
    refresh_token_enc = Column(Text, nullable=True)                 # TikTok OAuth token refresh
    account_currency = Column(String(10), nullable=False, default="USD")
    account_timezone = Column(String(100), nullable=False, default="UTC")
    is_active = Column(Boolean, default=True, nullable=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("client_id", "platform", "external_account_id", name="uq_ad_account_client_platform_external"),
    )
