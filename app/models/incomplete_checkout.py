from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String
from sqlalchemy.sql import func

from app.database import Base


class IncompleteCheckout(Base):
    __tablename__ = "incomplete_checkouts"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    visitor_id = Column(String(255), nullable=False)
    phone = Column(String(32), nullable=False)
    phone_hash = Column(String(64), nullable=False)
    customer_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(String(500), nullable=True)
    products = Column(JSON, nullable=True)
    amount = Column(Numeric(12, 2), nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="BDT")
    page_url = Column(String(1000), nullable=True)
    campaign_data = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    order_id = Column(String(255), nullable=True, index=True)
    last_activity_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    converted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_incomplete_client_status_activity", "client_id", "status", "last_activity_at"),
        Index("ix_incomplete_client_visitor_phone", "client_id", "visitor_id", "phone_hash"),
        Index("ix_incomplete_client_phone_status", "client_id", "phone_hash", "status"),
    )
