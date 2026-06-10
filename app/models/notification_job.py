from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.sql import func
from app.database import Base


class NotificationJob(Base):
    """WhatsApp owner notification jobs queue."""

    __tablename__ = "notification_jobs"
    __table_args__ = (
        Index("ix_notification_jobs_claim", "status", "attempt_count", "next_attempt_at", "locked_until"),
    )

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    whatsapp_instance_id = Column(Integer, ForeignKey("whatsapp_instances.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(50), nullable=False)  # purchase / incomplete_checkout
    channel = Column(String(50), nullable=False, default="whatsapp")
    provider = Column(String(50), nullable=False, default="evolution")
    payload = Column(JSON, nullable=False)
    message_text = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="pending", index=True)  # pending, processing, sent, failed
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=4)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    locked_by = Column(String(255), nullable=True)
    locked_until = Column(DateTime(timezone=True), nullable=True, index=True)
    dedupe_key = Column(String(255), unique=True, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
