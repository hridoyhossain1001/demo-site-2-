from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func
from app.database import Base


class WhatsAppInstance(Base):
    """WhatsApp API connection instance metadata and status."""

    __tablename__ = "whatsapp_instances"

    id = Column(Integer, primary_key=True, index=True)
    instance_name = Column(String(255), unique=True, nullable=False)
    phone_number = Column(String(50), nullable=True)
    provider = Column(String(50), nullable=False, default="evolution")
    base_url = Column(String(500), nullable=True)
    status = Column(String(50), nullable=False, default="active")
    client_count = Column(Integer, nullable=False, default=0)
    last_health_check_at = Column(DateTime(timezone=True), nullable=True)
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
