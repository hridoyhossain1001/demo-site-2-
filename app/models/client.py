import secrets
from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)                         # ক্লায়েন্টের নাম
    api_key = Column(String, unique=True, nullable=False,          # অটো-জেনারেটেড API Key
                     default=lambda: secrets.token_urlsafe(32))
    old_api_key = Column(String, unique=True, nullable=True)      # rotated key for grace period
    api_key_rotated_at = Column(DateTime(timezone=True), nullable=True) # key rotation timestamp
    public_key = Column(String, unique=True, nullable=False,       # Browser-safe tracker key
                        default=lambda: secrets.token_urlsafe(24))
    portal_key = Column(String, unique=True, nullable=True)        # Client portal login secret
    pixel_id = Column(String, nullable=False)                      # Facebook Pixel ID
    access_token = Column(String, nullable=False)                  # CAPI Access Token (encrypted)
    test_event_code = Column(String, nullable=True)               # FB Test Event Code (optional)
    is_active = Column(Boolean, default=True, nullable=False)      # ক্লায়েন্ট অ্যাক্টিভ কিনা
    domain = Column(String, nullable=True)                         # ক্লায়েন্টের ওয়েবসাইট ডোমেইন
    rate_limit = Column(Integer, default=5000, nullable=False)     # প্রতি মিনিটে সর্বোচ্চ রিকোয়েস্ট
    daily_quota = Column(Integer, default=100000, nullable=False)  # প্রতিদিন সর্বোচ্চ ইভেন্ট
    enable_facebook = Column(Boolean, default=True, nullable=False)
    enable_tiktok = Column(Boolean, default=True, nullable=False)
    enable_ga4 = Column(Boolean, default=True, nullable=False)
    # ─── TikTok CAPI Integration ──────────────────────────────────────────
    tiktok_pixel_id = Column(String, nullable=True)               # TikTok Pixel ID (optional)
    tiktok_access_token = Column(String, nullable=True)           # TikTok Access Token (encrypted, optional)
    tiktok_test_event_code = Column(String, nullable=True)        # TikTok Test Event Code (ফেসবুক থেকে আলাদা)
    # ─── GA4 Server-Side Integration ──────────────────────────────────────
    ga4_measurement_id = Column(String, nullable=True)            # GA4 Measurement ID (e.g. G-XXXXX)
    ga4_api_secret = Column(String, nullable=True)                # GA4 API Secret (encrypted)
    # ─── Deferred Purchase ──────────────────────────────────────────────
    deferred_purchase = Column(Boolean, default=False, nullable=False) # ON হলে Purchase event হোল্ড হবে
    auto_confirm_days = Column(Integer, default=0, nullable=False)  # ০ = অফ, অন্যথা অর্ডারের বয়স N দিন হলে অটো-কনফার্ম
    auto_confirm_status = Column(String, default="completed")       # কনফার্ম অর্ডারের স্ট্যাটাস
    # ─── Webhook (Outbound) ────────────────────────────────────────────
    webhook_url = Column(String, nullable=True)                    # Custom Webhook URL (outbound)
    shopify_shared_secret = Column(String, nullable=True)          # Shopify Webhook Shared Secret

    # ─── Monthly Usage Limit (Rate Limiting Per-Client) ───────────────
    monthly_limit = Column(Integer, default=50000, nullable=False)  # মাসিক সর্বোচ্চ ইভেন্ট (0 = unlimited)
    event_rules = Column(JSON, nullable=True)                      # Custom routing rules for events (JSON)
    resolved_suggestions = Column(JSON, nullable=True)
    dismissed_suggestions = Column(JSON, nullable=True)
    portal_seen_state = Column(JSON, nullable=True)                # Client portal notification seen timestamps
    # ─── Courier Integration ─────────────────────────────────────────────
    pathao_api_key = Column(String, nullable=True)                 # Pathao Merchant API Key
    pathao_secret_key = Column(String, nullable=True)              # Pathao Secret (encrypted)
    pathao_store_id = Column(String, nullable=True)                # Pathao Store ID
    pathao_environment = Column(String, default="live", nullable=False) # live / sandbox
    pathao_webhook_secret = Column(String, nullable=True)          # Pathao webhook secret (encrypted)
    pathao_webhook_verified_at = Column(DateTime(timezone=True), nullable=True)
    steadfast_api_key = Column(String, nullable=True)              # SteadFast API Key
    steadfast_secret_key = Column(String, nullable=True)            # SteadFast Secret (encrypted)
    steadfast_webhook_token = Column(String, nullable=True)        # SteadFast webhook bearer token (encrypted)
    redx_access_token = Column(String, nullable=True)              # RedX bearer token (encrypted)
    redx_webhook_secret = Column(String, nullable=True)             # RedX callback query token (encrypted)
    redx_pickup_store_id = Column(String, nullable=True)           # RedX default pickup store ID
    redx_delivery_area_id = Column(String, nullable=True)          # RedX default delivery area ID
    redx_delivery_area_name = Column(String, nullable=True)        # RedX default delivery area name
    courier_auto_send = Column(Boolean, default=False, nullable=False) # Confirm করলেই অটো Courier-এ পাঠাবে?
    default_courier = Column(String, nullable=True)                # 'pathao' / 'steadfast' / 'redx'
    plan_tier = Column(String, default="growth", nullable=False)   # free / growth / scale / agency
    billing_status = Column(String, default="paid", nullable=False) # trial / paid / pending_payment / overdue / manual_invoice / free
    trial_started_at = Column(DateTime(timezone=True), nullable=True)
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)

    # ─── WhatsApp Notifications Settings ──────────────────────────────────
    owner_notify_whatsapp = Column(Boolean, default=False, nullable=False)
    owner_whatsapp_number = Column(String(50), nullable=True)
    whatsapp_instance_id = Column(Integer, ForeignKey("whatsapp_instances.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
