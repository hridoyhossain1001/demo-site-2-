from sqlalchemy import Boolean, Column, Integer, String, DateTime, Text, ForeignKey, Float
from sqlalchemy.sql import func
from app.database import Base


class EventLog(Base):
    """à¦ªà§à¦°à¦¤à¦¿à¦Ÿà¦¿ à¦‡à¦­à§‡à¦¨à§à¦Ÿ à¦•à¦²à§‡à¦° à¦²à¦— â€” à¦¡à¦¿à¦¬à¦¾à¦—à¦¿à¦‚, à¦…à§à¦¯à¦¾à¦¨à¦¾à¦²à¦¿à¦Ÿà¦¿à¦•à§à¦¸ à¦“ à¦¬à¦¿à¦²à¦¿à¦‚-à¦à¦° à¦œà¦¨à§à¦¯"""
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    event_name = Column(String, nullable=False, index=True)  # PageView, Purchase, etc.
    event_id = Column(String, nullable=True, index=True)     # Deduplication key (client_id + event_id = unique)
    event_count = Column(Integer, default=1)                 # à¦à¦•à¦¬à¦¾à¦°à§‡ à¦•à¦¯à¦¼à¦Ÿà¦¿ à¦‡à¦­à§‡à¦¨à§à¦Ÿ à¦ªà¦¾à¦ à¦¾à¦¨à§‹ à¦¹à¦¯à¦¼à§‡à¦›à§‡
    status = Column(String, nullable=False, default="success")  # success / failed
    fb_response = Column(Text, nullable=True)                # Facebook-à¦à¦° response JSON
    error_message = Column(Text, nullable=True)              # Error à¦¹à¦²à§‡ message
    ip_address = Column(String, nullable=True)               # à¦°à¦¿à¦•à§‹à¦¯à¦¼à§‡à¦¸à§à¦Ÿà§‡à¦° IP
    visitor_key = Column(String(80), nullable=True, index=True)
    geo_country = Column(String(8), nullable=True, index=True)
    geo_region = Column(String(80), nullable=True)
    geo_city = Column(String(80), nullable=True)
    geo_district = Column(String(80), nullable=True)
    device_type = Column(String(24), nullable=True)
    device_os = Column(String(40), nullable=True)
    device_browser = Column(String(40), nullable=True)
    screen_width = Column(Integer, nullable=True)
    screen_height = Column(Integer, nullable=True)
    emq_score = Column(Float, nullable=True)                 # Event Match Quality Score (0-10)
    value = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    campaign_source = Column(String, nullable=True, index=True)
    utm_source = Column(String, nullable=True, index=True)
    utm_medium = Column(String, nullable=True)
    utm_campaign = Column(String, nullable=True, index=True)
    utm_content = Column(String, nullable=True)
    utm_term = Column(String, nullable=True)
    has_content_ids = Column(Boolean, nullable=False, default=False)
    has_contents = Column(Boolean, nullable=False, default=False)
    has_value = Column(Boolean, nullable=False, default=False)
    has_currency = Column(Boolean, nullable=False, default=False)
    has_user_match = Column(Boolean, nullable=False, default=False)
    has_email_phone = Column(Boolean, nullable=False, default=False)
    has_click_id = Column(Boolean, nullable=False, default=False)
    has_event_id = Column(Boolean, nullable=False, default=False)
    has_utm = Column(Boolean, nullable=False, default=False)

    # Attribution Keys
    ad_platform = Column(String(30), nullable=True, index=True)
    ad_campaign_id = Column(String(100), nullable=True, index=True)
    ad_adset_id = Column(String(100), nullable=True, index=True)
    ad_id = Column(String(100), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    from sqlalchemy import Index
    __table_args__ = (
        Index("ix_event_logs_analytics", "client_id", "event_name", "created_at"),
        Index("ix_event_logs_campaign", "client_id", "utm_source", "utm_campaign", "created_at"),
        Index("ix_event_logs_geo_district", "client_id", "geo_district", "created_at"),
        Index("ix_event_logs_visitor_funnel", "client_id", "geo_district", "event_name", "visitor_key", "created_at"),
        Index("ix_event_logs_device_type", "client_id", "device_type", "created_at"),
        Index("ix_event_logs_ad_campaign", "client_id", "ad_campaign_id", "created_at"),
    )
