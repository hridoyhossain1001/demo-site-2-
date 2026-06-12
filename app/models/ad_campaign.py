from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base

class AdCampaign(Base):
    __tablename__ = "ad_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    ad_account_id = Column(Integer, ForeignKey("ad_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(30), nullable=False)
    external_campaign_id = Column(String(100), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=True)                        # ACTIVE, PAUSED, etc.
    objective = Column(String(50), nullable=True)                     # CONVERSIONS, CLICKS, etc.
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("ad_account_id", "external_campaign_id", name="uq_ad_campaign_account_external"),
    )
