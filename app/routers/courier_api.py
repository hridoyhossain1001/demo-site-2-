import logging
import secrets
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from datetime import datetime, timezone

from app.database import get_db
from app.models.client import Client
from app.models.pending_event import PendingEvent
from app.models.courier_order import CourierOrder
from app.routers.client_api import get_current_portal_client
from app.security import encrypt_token, decrypt_token
from app.services.courier_booking_service import cancel_queued_booking, enqueue_courier_booking
from app.services.courier_service import CourierService
from app.services.plan_service import has_growth_access, require_growth_access
from app.utils.display import mask_secret

logger = logging.getLogger(__name__)
router = APIRouter()

# ─── Pydantic Schemas ────────────────────────────────────────────────────────
class CourierSettingsResponse(BaseModel):
    pathao_api_key: Optional[str] = None
    pathao_secret_key: Optional[str] = None
    pathao_client_id: Optional[str] = None
    pathao_email: Optional[str] = None
    pathao_client_secret: Optional[str] = None
    pathao_password: Optional[str] = None
    pathao_store_id: Optional[str] = None
    pathao_environment: str = "live"
    pathao_webhook_secret: Optional[str] = None
    pathao_webhook_secret_configured: bool = False
    pathao_webhook_verified_at: Optional[str] = None
    steadfast_api_key: Optional[str] = None
    steadfast_secret_key: Optional[str] = None
    steadfast_webhook_token_configured: bool = False
    redx_access_token: Optional[str] = None
    redx_webhook_secret_configured: bool = False
    redx_pickup_store_id: Optional[str] = None
    redx_delivery_area_id: Optional[str] = None
    redx_delivery_area_name: Optional[str] = None
    courier_auto_send: bool
    default_courier: Optional[str] = None

class CourierSettingsUpdate(BaseModel):
    pathao_api_key: Optional[str] = None
    pathao_secret_key: Optional[str] = None
    pathao_client_id: Optional[str] = None
    pathao_email: Optional[str] = None
    pathao_client_secret: Optional[str] = None
    pathao_password: Optional[str] = None
    pathao_store_id: Optional[str] = None
    pathao_environment: Optional[str] = None
    pathao_webhook_secret: Optional[str] = None
    steadfast_api_key: Optional[str] = None
    steadfast_secret_key: Optional[str] = None
    redx_access_token: Optional[str] = None
    redx_pickup_store_id: Optional[str] = None
    redx_delivery_area_id: Optional[str] = None
    redx_delivery_area_name: Optional[str] = None
    courier_auto_send: bool
    default_courier: Optional[str] = None

class SendToCourierRequest(BaseModel):
    pending_event_id: int
    courier_provider: str # 'pathao' or 'steadfast'
    recipient_name: str
    recipient_phone: str
    recipient_address: str
    cod_amount: float
    store_id: Optional[int] = None
    item_weight: Optional[float] = 0.5
    item_quantity: Optional[int] = 1
    delivery_area_id: Optional[int] = None
    delivery_area_name: Optional[str] = None
    pickup_store_id: Optional[int] = None
    recipient_city: Optional[int] = None
    recipient_zone: Optional[int] = None
    recipient_area: Optional[int] = None

class CourierOrderResponse(BaseModel):
    id: int
    order_id: str
    courier_provider: str
    courier_order_id: Optional[str] = None
    courier_tracking_id: Optional[str] = None
    courier_status: str
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    recipient_address: Optional[str] = None
    cod_amount: float
    delivery_charge: float
    created_at: str
    purchase_event_sent: bool
    products: Optional[list] = None

# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/courier/settings", response_model=CourierSettingsResponse)
async def get_courier_settings(client: Client = Depends(get_current_portal_client)):
    pathao_client_id = None
    pathao_email = None
    if client.pathao_api_key and "|" in client.pathao_api_key:
        pathao_client_id, pathao_email = client.pathao_api_key.split("|", 1)

    pathao_secret_display = None
    pathao_client_secret = None
    pathao_password = None
    if client.pathao_secret_key:
        decrypted_pathao_secret = decrypt_token(client.pathao_secret_key)
        pathao_secret_display = mask_secret(decrypted_pathao_secret)
        if "|" in decrypted_pathao_secret:
            raw_client_secret, raw_password = decrypted_pathao_secret.split("|", 1)
            pathao_client_secret = mask_secret(raw_client_secret)
            pathao_password = mask_secret(raw_password)

    # Decrypt secrets for display or mask them
    return CourierSettingsResponse(
        pathao_api_key=client.pathao_api_key,
        pathao_secret_key=pathao_secret_display,
        pathao_client_id=pathao_client_id,
        pathao_email=pathao_email,
        pathao_client_secret=pathao_client_secret,
        pathao_password=pathao_password,
        pathao_store_id=client.pathao_store_id,
        pathao_environment=client.pathao_environment or "live",
        pathao_webhook_secret=mask_secret(decrypt_token(client.pathao_webhook_secret)) if client.pathao_webhook_secret else None,
        pathao_webhook_secret_configured=bool(client.pathao_webhook_secret),
        pathao_webhook_verified_at=client.pathao_webhook_verified_at.isoformat() if client.pathao_webhook_verified_at else None,
        steadfast_api_key=client.steadfast_api_key,
        steadfast_secret_key=mask_secret(decrypt_token(client.steadfast_secret_key)) if client.steadfast_secret_key else None,
        steadfast_webhook_token_configured=bool(client.steadfast_webhook_token),
        redx_access_token=mask_secret(decrypt_token(client.redx_access_token)) if client.redx_access_token else None,
        redx_webhook_secret_configured=bool(client.redx_webhook_secret),
        redx_pickup_store_id=client.redx_pickup_store_id,
        redx_delivery_area_id=client.redx_delivery_area_id,
        redx_delivery_area_name=client.redx_delivery_area_name,
        courier_auto_send=has_growth_access(client) and client.courier_auto_send,
        default_courier=client.default_courier
    )

@router.post("/courier/settings")
async def update_courier_settings(
    settings: CourierSettingsUpdate,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    # Update fields. If secret key is provided and is not masked, encrypt it.
    masked_tokens = ("â€¢", "•", "*")

    def looks_masked(value: Optional[str]) -> bool:
        if not value:
            return False
        return any(token in value for token in masked_tokens)

    if settings.pathao_client_id is not None or settings.pathao_email is not None:
        client_id = (settings.pathao_client_id or "").strip()
        email = (settings.pathao_email or "").strip()
        client.pathao_api_key = f"{client_id}|{email}" if client_id or email else None
    elif settings.pathao_api_key is not None:
        client.pathao_api_key = settings.pathao_api_key.strip() or None
    if settings.pathao_store_id is not None:
        client.pathao_store_id = settings.pathao_store_id.strip() or None
    if settings.pathao_environment is not None:
        environment = settings.pathao_environment.strip().lower()
        if environment not in ("live", "sandbox"):
            raise HTTPException(status_code=400, detail="Pathao environment must be live or sandbox.")
        client.pathao_environment = environment
    if settings.pathao_webhook_secret is not None and not looks_masked(settings.pathao_webhook_secret):
        client.pathao_webhook_secret = encrypt_token(settings.pathao_webhook_secret.strip()) if settings.pathao_webhook_secret.strip() else None
        client.pathao_webhook_verified_at = None
    if settings.steadfast_api_key is not None:
        client.steadfast_api_key = settings.steadfast_api_key.strip() or None
    if settings.default_courier is not None:
        client.default_courier = settings.default_courier.strip() or None
    if settings.redx_pickup_store_id is not None:
        client.redx_pickup_store_id = settings.redx_pickup_store_id.strip() or None
    if settings.redx_delivery_area_id is not None:
        client.redx_delivery_area_id = settings.redx_delivery_area_id.strip() or None
    if settings.redx_delivery_area_name is not None:
        client.redx_delivery_area_name = settings.redx_delivery_area_name.strip() or None
        
    if settings.courier_auto_send:
        require_growth_access(client, "Automatic courier booking")
    client.courier_auto_send = has_growth_access(client) and settings.courier_auto_send
    
    # Encrypt credentials if they are newly updated and not masked
    if settings.pathao_client_secret is not None or settings.pathao_password is not None:
        current_secret = decrypt_token(client.pathao_secret_key) if client.pathao_secret_key else ""
        current_client_secret = ""
        current_password = ""
        if "|" in current_secret:
            current_client_secret, current_password = current_secret.split("|", 1)

        new_client_secret = (settings.pathao_client_secret or "").strip()
        new_password = (settings.pathao_password or "").strip()
        if looks_masked(new_client_secret):
            new_client_secret = current_client_secret
        if looks_masked(new_password):
            new_password = current_password

        client.pathao_secret_key = encrypt_token(f"{new_client_secret}|{new_password}") if new_client_secret or new_password else None
    if settings.pathao_secret_key and not (settings.pathao_secret_key.startswith("•••") or "••••••••••••" in settings.pathao_secret_key):
        client.pathao_secret_key = encrypt_token(settings.pathao_secret_key.strip())
    elif settings.pathao_secret_key == "":
        client.pathao_secret_key = None
        
    if settings.steadfast_secret_key and not (settings.steadfast_secret_key.startswith("•••") or "••••••••••••" in settings.steadfast_secret_key):
        client.steadfast_secret_key = encrypt_token(settings.steadfast_secret_key.strip())
    elif settings.steadfast_secret_key == "":
        client.steadfast_secret_key = None
    if settings.redx_access_token and not looks_masked(settings.redx_access_token):
        client.redx_access_token = encrypt_token(settings.redx_access_token.strip())
    elif settings.redx_access_token == "":
        client.redx_access_token = None
        
    db.add(client)
    await db.commit()
    
    # Clear client cache so change takes effect immediately
    from app.dependencies import clear_client_cache
    clear_client_cache(client.api_key)
    
    return {"message": "Courier settings updated successfully."}


@router.post("/courier/pathao/webhook-secret")
async def get_or_create_pathao_webhook_secret(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    if client.pathao_webhook_secret:
        secret = decrypt_token(client.pathao_webhook_secret)
    else:
        secret = secrets.token_urlsafe(32)
        client.pathao_webhook_secret = encrypt_token(secret)
        client.pathao_webhook_verified_at = None
        db.add(client)
        await db.commit()

    return {
        "secret": secret,
        "configured": True,
        "verified_at": client.pathao_webhook_verified_at.isoformat() if client.pathao_webhook_verified_at else None,
    }


@router.post("/courier/{provider}/webhook-secret")
async def get_or_create_courier_webhook_secret(
    provider: str,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    provider = provider.strip().lower()
    fields = {
        "steadfast": "steadfast_webhook_token",
        "redx": "redx_webhook_secret",
    }
    field = fields.get(provider)
    if not field:
        raise HTTPException(status_code=400, detail="Unsupported courier webhook provider.")

    encrypted_secret = getattr(client, field)
    if encrypted_secret:
        secret = decrypt_token(encrypted_secret)
    else:
        secret = secrets.token_urlsafe(32)
        setattr(client, field, encrypt_token(secret))
        db.add(client)
        await db.commit()

    callback_url = f"https://api.buykori.app/api/v1/webhook/{provider}"
    if provider == "redx":
        callback_url += f"?token={secret}"
    return {"secret": secret, "configured": True, "callback_url": callback_url}


@router.post("/courier/send", status_code=202)
async def send_order_to_courier(
    req: SendToCourierRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    # Retrieve pending event and lock the row to prevent concurrent double requests
    event_result = await db.execute(
        select(PendingEvent).where(
            (PendingEvent.id == req.pending_event_id) & (PendingEvent.client_id == client.id)
        ).with_for_update()
    )
    pending_event = event_result.scalar_one_or_none()
    if not pending_event:
        raise HTTPException(status_code=404, detail="Order (pending event) not found.")

    try:
        booking = await enqueue_courier_booking(
            db,
            client=client,
            pending=pending_event,
            provider=req.courier_provider,
            overrides={
                "recipient_name": req.recipient_name,
                "recipient_phone": req.recipient_phone,
                "recipient_address": req.recipient_address,
                "cod_amount": req.cod_amount,
                "store_id": req.store_id,
                "item_weight": req.item_weight,
                "item_quantity": req.item_quantity,
                "delivery_area_id": req.delivery_area_id,
                "delivery_area_name": req.delivery_area_name,
                "pickup_store_id": req.pickup_store_id,
                "recipient_city": req.recipient_city,
                "recipient_zone": req.recipient_zone,
                "recipient_area": req.recipient_area,
            },
            purchase_event_sent=pending_event.portal_state == "operations_only",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    courier_order = booking["courier_order"]
    await db.commit()
    return {
        "success": True,
        "queued": booking["mode"] == "queued",
        "message": booking["message"],
        "courier_order_db_id": courier_order.id,
        "tracking_id": courier_order.courier_tracking_id,
        "courier_order_id": courier_order.courier_order_id,
    }


@router.get("/courier/orders", response_model=List[CourierOrderResponse])
async def get_courier_orders(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    result = await db.execute(
        select(CourierOrder, PendingEvent.raw_order_data, PendingEvent.event_data)
        .outerjoin(PendingEvent, CourierOrder.pending_event_id == PendingEvent.id)
        .where(CourierOrder.client_id == client.id)
        .order_by(desc(CourierOrder.created_at))
        .offset(offset)
        .limit(limit)
    )
    rows = result.all()
    
    response = []
    for order, raw_order_data, event_data in rows:
        # Extract products from raw_order_data if available
        products_list = []
        if raw_order_data and isinstance(raw_order_data, dict):
            # 1. line_items or products from raw_order_data (WooCommerce order data)
            line_items = raw_order_data.get("line_items") or raw_order_data.get("products") or []
            if isinstance(line_items, list):
                for item in line_items:
                    if isinstance(item, dict):
                        raw_name = item.get("name") or item.get("title") or item.get("product_name") or ""
                        item_id = str(item.get("product_id") or item.get("id") or "")
                        display_name = raw_name if raw_name else f"Product #{item_id}" if item_id else "Unknown Product"
                        products_list.append({
                            "name": display_name,
                            "quantity": int(item.get("quantity") or 1),
                            "price": float(item.get("subtotal") or item.get("price") or 0),
                        })
            # 2. raw_order_data might have custom_data.contents (Facebook CAPI format)
            if not products_list:
                custom_data = raw_order_data.get("custom_data", {}) or {}
                contents = custom_data.get("contents", []) or []
                if isinstance(contents, list):
                    for item in contents:
                        if isinstance(item, dict):
                            raw_name = item.get("title") or item.get("name") or item.get("product_name") or item.get("content_name") or ""
                            item_id = str(item.get("id") or item.get("content_id") or "")
                            display_name = raw_name if raw_name else f"Product #{item_id}" if item_id else "Unknown Product"
                            products_list.append({
                                "name": display_name,
                                "quantity": int(item.get("quantity") or item.get("qty") or 1),
                                "price": float(item.get("item_price") or item.get("price") or 0),
                            })

        # 3. Fallback: extract from event_data (CAPI payload stored separately)
        if not products_list and event_data and isinstance(event_data, dict):
            # event_data structure: { "data": [{ "custom_data": { "contents": [...] } }] }
            data_list = event_data.get("data") or []
            if isinstance(data_list, list) and data_list:
                first_event = data_list[0] if isinstance(data_list[0], dict) else {}
                cdata = first_event.get("custom_data", {}) or {}
            else:
                cdata = event_data.get("custom_data", {}) or {}
            contents = cdata.get("contents") or []
            if isinstance(contents, list):
                for item in contents:
                    if isinstance(item, dict):
                        raw_name = item.get("title") or item.get("name") or item.get("product_name") or item.get("content_name") or ""
                        item_id = str(item.get("id") or item.get("content_id") or "")
                        display_name = raw_name if raw_name else f"Product #{item_id}" if item_id else "Unknown Product"
                        products_list.append({
                            "name": display_name,
                            "quantity": int(item.get("quantity") or item.get("qty") or 1),
                            "price": float(item.get("item_price") or item.get("price") or 0),
                        })

        # 4. Last resort: num_items fallback
        if not products_list and raw_order_data and isinstance(raw_order_data, dict):
            custom_data = raw_order_data.get("custom_data", {}) or {}
            if custom_data.get("num_items"):
                products_list.append({
                    "name": "Product (details not available)",
                    "quantity": int(custom_data.get("num_items", 1)),
                    "price": float(custom_data.get("value", 0)),
                })
        
        products = products_list if products_list else None

        response.append(CourierOrderResponse(
            id=order.id,
            order_id=order.order_id,
            courier_provider=order.courier_provider,
            courier_order_id=order.courier_order_id,
            courier_tracking_id=order.courier_tracking_id,
            courier_status=order.courier_status,
            recipient_name=order.recipient_name,
            recipient_phone=order.recipient_phone,
            recipient_address=order.recipient_address,
            cod_amount=order.cod_amount,
            delivery_charge=order.delivery_charge,
            created_at=order.created_at.isoformat(),
            purchase_event_sent=order.purchase_event_sent,
            products=products
        ))
    return response


# ─── Cancel Courier Order ────────────────────────────────────────────────────

@router.post("/courier/cancel/{order_db_id}")
async def cancel_courier_order(
    order_db_id: int,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Pathao বা SteadFast-এ পাঠানো order cancel করা।
    - শুধুমাত্র 'pending' বা 'in_transit' status-এর orders cancel করা যাবে।
    - Pathao: API call করে cancel, SteadFast: local-only cancel।
    - Cancel হলে PendingEvent-এর status 'cancelled'-এ আপডেট হবে।
    """
    # Courier order খোঁজা
    result = await db.execute(
        select(CourierOrder).where(
            (CourierOrder.id == order_db_id) & (CourierOrder.client_id == client.id)
        ).with_for_update()
    )
    courier_order = result.scalar_one_or_none()
    if not courier_order:
        raise HTTPException(status_code=404, detail="Courier order not found.")

    # Already cancelled/delivered check
    current_status = (courier_order.courier_status or "").lower()
    if current_status in ("cancelled", "delivered", "returned"):
        raise HTTPException(
            status_code=400,
            detail=f"Order cannot be cancelled — current status is '{current_status}'."
        )

    if current_status == "booking_processing":
        raise HTTPException(
            status_code=409,
            detail="Courier booking is being processed. Refresh shortly before cancelling.",
        )
    if current_status == "booking_queued":
        if not await cancel_queued_booking(db, courier_order):
            raise HTTPException(
                status_code=409,
                detail="Courier booking status changed. Refresh and try again.",
            )
        await db.commit()
        return {
            "success": True,
            "message": "Queued courier booking cancelled before provider dispatch.",
            "local_only": False,
            "order_id": courier_order.order_id,
            "courier_provider": courier_order.courier_provider,
        }

    cancel_result: dict = {}
    local_only = False

    if courier_order.courier_provider == "pathao":
        # Pathao credentials দিয়ে cancel call
        if not client.pathao_api_key or not client.pathao_secret_key:
            raise HTTPException(status_code=400, detail="Pathao API credentials are not configured.")
        try:
            client_id_str, email = client.pathao_api_key.split("|", 1)
            decrypted = decrypt_token(client.pathao_secret_key)
            client_secret, password = decrypted.split("|", 1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Pathao credentials format incorrect.")

        consignment_id = courier_order.courier_order_id or courier_order.courier_tracking_id
        if not consignment_id:
            raise HTTPException(status_code=400, detail="Courier order ID not found for this order.")

        cancel_result = await CourierService.cancel_pathao_order(
            client_id=client_id_str,
            client_secret=client_secret,
            email=email,
            password=password,
            consignment_id=consignment_id,
            base_url=CourierService.pathao_base_url(client.pathao_environment),
        )
        local_only = cancel_result.get("local_only", False)

        if not cancel_result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=f"Pathao cancel failed: {cancel_result.get('error', 'Unknown error')}"
            )

    elif courier_order.courier_provider == "steadfast":
        # SteadFast: no cancel API, local-only
        tracking_code = courier_order.courier_tracking_id or courier_order.courier_order_id or ""
        api_key = client.steadfast_api_key or ""
        secret_key = decrypt_token(client.steadfast_secret_key) if client.steadfast_secret_key else ""

        cancel_result = await CourierService.cancel_steadfast_order(
            api_key=api_key,
            secret_key=secret_key,
            tracking_code=tracking_code,
        )
        local_only = cancel_result.get("local_only", True)

    elif courier_order.courier_provider == "redx":
        if not client.redx_access_token:
            raise HTTPException(status_code=400, detail="RedX access token is not configured.")
        tracking_id = courier_order.courier_tracking_id or courier_order.courier_order_id
        if not tracking_id:
            raise HTTPException(status_code=400, detail="RedX tracking ID not found for this order.")
        cancel_result = await CourierService.cancel_redx_order(
            access_token=decrypt_token(client.redx_access_token),
            tracking_id=tracking_id,
        )
        if not cancel_result.get("success"):
            raise HTTPException(status_code=400, detail=f"RedX cancel failed: {cancel_result.get('error', 'Unknown error')}")
        local_only = False

    else:
        raise HTTPException(status_code=400, detail="Unsupported courier provider.")

    # DB update — courier order status cancel করা
    courier_order.courier_status = "cancelled"
    history = courier_order.status_history or []
    if not isinstance(history, list):
        history = []
    history.append({
        "status": "cancelled",
        "raw_status": "cancelled_by_merchant",
        "time": datetime.now(timezone.utc).isoformat(),
        "local_only": local_only,
    })
    courier_order.status_history = history
    db.add(courier_order)

    # PendingEvent status update
    if courier_order.pending_event_id:
        from app.models.pending_event import PendingEvent
        pe_result = await db.execute(
            select(PendingEvent).where(PendingEvent.id == courier_order.pending_event_id)
        )
        pending_event = pe_result.scalar_one_or_none()
        if pending_event:
            pending_event.status = "cancelled"
            pending_event.portal_state = "cancelled"
            db.add(pending_event)

    await db.commit()

    msg = cancel_result.get("message", f"Order cancelled successfully from {courier_order.courier_provider.capitalize()}.")
    return {
        "success": True,
        "message": msg,
        "local_only": local_only,
        "order_id": courier_order.order_id,
        "courier_provider": courier_order.courier_provider,
    }


@router.get("/courier/pathao/stores")
async def get_pathao_stores(
    client: Client = Depends(get_current_portal_client),
):
    if not client.pathao_api_key or not client.pathao_secret_key:
        raise HTTPException(
            status_code=400,
            detail="Pathao API credentials are not configured. Please set them in Settings."
        )

    try:
        client_id, email = client.pathao_api_key.split("|", 1)
        decrypted_secret_pass = decrypt_token(client.pathao_secret_key)
        client_secret, password = decrypted_secret_pass.split("|", 1)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Pathao credentials format incorrect. Expected client_id|email and client_secret|password."
        )

    stores = await CourierService.get_pathao_stores(
        client_id=client_id,
        client_secret=client_secret,
        email=email,
        password=password,
        base_url=CourierService.pathao_base_url(client.pathao_environment),
    )
    return stores


async def _get_pathao_location_credentials(client: Client) -> tuple[str, str]:
    if not client.pathao_api_key or not client.pathao_secret_key:
        raise HTTPException(
            status_code=400,
            detail="Pathao API credentials are not configured. Please set them in Settings."
        )
    try:
        client_id, email = client.pathao_api_key.split("|", 1)
        client_secret, password = decrypt_token(client.pathao_secret_key).split("|", 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Pathao credentials format is incorrect.") from exc
    token = await CourierService.get_pathao_token(
        client_id,
        client_secret,
        email,
        password,
        base_url=CourierService.pathao_base_url(client.pathao_environment),
    )
    return token, CourierService.pathao_base_url(client.pathao_environment)


@router.get("/courier/pathao/cities")
async def get_pathao_cities(client: Client = Depends(get_current_portal_client)):
    token, base_url = await _get_pathao_location_credentials(client)
    return await CourierService._get_pathao_cities(token, base_url=base_url)


@router.get("/courier/pathao/zones")
async def get_pathao_zones(city_id: int, client: Client = Depends(get_current_portal_client)):
    token, base_url = await _get_pathao_location_credentials(client)
    return await CourierService._get_pathao_zones(token, city_id, base_url=base_url)


@router.get("/courier/pathao/areas")
async def get_pathao_areas(zone_id: int, client: Client = Depends(get_current_portal_client)):
    token, base_url = await _get_pathao_location_credentials(client)
    return await CourierService._get_pathao_areas(token, zone_id, base_url=base_url)


@router.get("/courier/redx/areas")
async def get_redx_areas(
    client: Client = Depends(get_current_portal_client),
):
    if not client.redx_access_token:
        raise HTTPException(status_code=400, detail="RedX access token is not configured. Please set it in Settings.")
    return await CourierService.get_redx_areas(decrypt_token(client.redx_access_token))
