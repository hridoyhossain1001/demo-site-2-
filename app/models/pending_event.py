from sqlalchemy import Boolean, Column, Integer, String, DateTime, JSON, ForeignKey, UniqueConstraint, Index, text
from sqlalchemy.sql import func
from app.database import Base


class PendingEvent(Base):
    """
    Deferred Purchase Events — অর্ডার কনফার্ম না হওয়া পর্যন্ত হোল্ড থাকবে।
    কনফার্ম হলে Facebook-এ পাঠানো হবে, ক্যান্সেল হলে ডিলিট।
    ৭ দিনের বেশি পুরোনো pending events auto-expire হবে।
    """
    __tablename__ = "pending_events"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id = Column(String, nullable=False, index=True)           # "order-12345"
    event_data = Column(JSON, nullable=False)                        # সম্পূর্ণ event payload (user_data সহ)
    raw_order_data = Column(JSON, nullable=True)                     # raw courier payload: name, phone, address, COD
    status = Column(String, default="pending", index=True)           # pending / courier_booked / confirmed / cancelled / expired
    portal_state = Column(String, nullable=True)                     # optional UI state for courier/deferred workflow
    is_confirmed = Column(Boolean, default=False, nullable=False)    # true once merchant accepts the order
    is_deleted = Column(Boolean, default=False, nullable=False)      # soft-delete column to allow reusing order IDs
    fraud_score = Column(Integer, nullable=True)                     # Fraud Risk Score (0-100)
    fraud_details = Column(JSON, nullable=True)                      # Heuristics evaluation details JSON
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    confirmed_at = Column(DateTime(timezone=True), nullable=True)

    # একই ক্লায়েন্টের একই order_id duplicate হতে পারবে না active/pending অবস্থায়
    __table_args__ = (
        Index(
            'uq_client_order_active',
            'client_id',
            'order_id',
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'")
        ),
        # expiry_service ও cleanup_service-এর জন্য composite index — full table scan এড়াতে
        Index('ix_pending_client_status_created', 'client_id', 'status', 'created_at'),
        Index('ix_pending_client_created', 'client_id', 'created_at'),
    )
