from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, ForeignKey, BigInteger, Index
from sqlalchemy.sql import func
from app.database import Base

class AdInsightDaily(Base):
    __tablename__ = "ad_insights_daily"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(30), nullable=False)
    external_campaign_id = Column(String(100), nullable=False, index=True)
    insight_date = Column(Date, nullable=False, index=True)
    spend = Column(Numeric(12, 4), nullable=False, default=0.0)      # Daily spend
    impressions = Column(BigInteger, nullable=False, default=0)
    clicks = Column(BigInteger, nullable=False, default=0)
    platform_purchases = Column(Integer, nullable=True)              # Conversions reported by platform
    platform_revenue = Column(Numeric(12, 4), nullable=True)         # Revenue reported by platform
    currency = Column(String(10), nullable=False, default="USD")
    synced_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_ad_insights_lookup", "client_id", "platform", "external_campaign_id", "insight_date", unique=True),
    )
