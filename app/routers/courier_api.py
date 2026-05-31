import logging
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
from app.services.courier_service import CourierService
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
    steadfast_api_key: Optional[str] = None
    steadfast_secret_key: Optional[str] = None
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
    steadfast_api_key: Optional[str] = None
    steadfast_secret_key: Optional[str] = None
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
        steadfast_api_key=client.steadfast_api_key,
        steadfast_secret_key=mask_secret(decrypt_token(client.steadfast_secret_key)) if client.steadfast_secret_key else None,
        courier_auto_send=client.courier_auto_send,
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
    if settings.steadfast_api_key is not None:
        client.steadfast_api_key = settings.steadfast_api_key.strip() or None
    if settings.default_courier is not None:
        client.default_courier = settings.default_courier.strip() or None
        
    client.courier_auto_send = settings.courier_auto_send
    
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
        
    db.add(client)
    await db.commit()
    
    # Clear client cache so change takes effect immediately
    from app.dependencies import clear_client_cache
    clear_client_cache(client.api_key)
    
    return {"message": "Courier settings updated successfully."}

@router.post("/courier/send")
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
        
    # Check if this order is already sent — with_for_update() দিয়ে race condition রোধ করা হচ্ছে
    # (concurrent request দুটি একই সাথে pass করে double booking করতে পারবে না)
    order_exist = await db.execute(
        select(CourierOrder).where(
            (CourierOrder.client_id == client.id) & (CourierOrder.order_id == pending_event.order_id)
        ).with_for_update(skip_locked=False)
    )
    if order_exist.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="This order has already been sent to a courier.")

    result = {}
    
    if req.courier_provider == "steadfast":
        if not client.steadfast_api_key or not client.steadfast_secret_key:
            raise HTTPException(status_code=400, detail="SteadFast API credentials are not configured.")
            
        api_key = client.steadfast_api_key
        secret_key = decrypt_token(client.steadfast_secret_key)
        
        result = await CourierService.send_to_steadfast(
            api_key=api_key,
            secret_key=secret_key,
            recipient_name=req.recipient_name,
            recipient_phone=req.recipient_phone,
            recipient_address=req.recipient_address,
            cod_amount=req.cod_amount,
            merchant_order_id=pending_event.order_id
        )
        
    elif req.courier_provider == "pathao":
        # Pathao credentials format:
        # pathao_api_key = "client_id|email"
        # pathao_secret_key = "client_secret|password" (encrypted)
        # pathao_store_id = store_id
        if not client.pathao_api_key or not client.pathao_secret_key:
            raise HTTPException(status_code=400, detail="Pathao API credentials are not configured.")
            
        store_id_to_use = str(req.store_id) if req.store_id is not None else client.pathao_store_id
        if not store_id_to_use:
            raise HTTPException(status_code=400, detail="Pathao Store ID is not configured.")
            
        try:
            client_id, email = client.pathao_api_key.split("|", 1)
            decrypted_secret_pass = decrypt_token(client.pathao_secret_key)
            client_secret, password = decrypted_secret_pass.split("|", 1)
        except ValueError:
            raise HTTPException(
                status_code=400, 
                detail="Pathao credentials format incorrect. Expected client_id|email and client_secret|password."
            )
            
        weight_to_use = req.item_weight if req.item_weight is not None else 0.5
        qty_to_use = req.item_quantity if req.item_quantity is not None else 1

        # Extract product names from event_data or raw_order_data
        item_description_to_use = None
        event_data_dict = pending_event.event_data or {}
        custom_data_dict = event_data_dict.get("custom_data") or {}
        contents_list = custom_data_dict.get("contents") or []

        if not contents_list and pending_event.raw_order_data:
            contents_list = pending_event.raw_order_data.get("products") or pending_event.raw_order_data.get("line_items") or []

        if contents_list and isinstance(contents_list, list):
            product_descriptions = []
            for item in contents_list:
                if isinstance(item, dict):
                    name = item.get("title") or item.get("name") or item.get("content_name")
                    qty = item.get("quantity") or item.get("qty") or 1
                    if name:
                        product_descriptions.append(f"{name} x{qty}")
            if product_descriptions:
                item_description_to_use = ", ".join(product_descriptions)
            
        result = await CourierService.send_to_pathao(
            client_id=client_id,
            client_secret=client_secret,
            email=email,
            password=password,
            store_id=store_id_to_use,
            recipient_name=req.recipient_name,
            recipient_phone=req.recipient_phone,
            recipient_address=req.recipient_address,
            cod_amount=req.cod_amount,
            merchant_order_id=pending_event.order_id,
            item_quantity=qty_to_use,
            item_weight=weight_to_use,
            item_description=item_description_to_use
        )
        
    else:
        raise HTTPException(status_code=400, detail="Unsupported courier provider.")

    if not result.get("success"):
        raise HTTPException(
            status_code=400, 
            detail=f"Courier service error: {result.get('error', 'Unknown error')}"
        )
        
    # Save the courier order
    courier_order = CourierOrder(
        client_id=client.id,
        pending_event_id=pending_event.id,
        order_id=pending_event.order_id,
        courier_provider=req.courier_provider,
        courier_order_id=result.get("courier_order_id"),
        courier_tracking_id=result.get("tracking_id"),
        courier_status="pending",
        recipient_name=req.recipient_name,
        recipient_phone=req.recipient_phone,
        recipient_address=req.recipient_address,
        cod_amount=req.cod_amount,
        status_history=[{"status": "pending", "time": datetime.now(timezone.utc).isoformat()}]
    )
    
    # Update pending event state (to show it has been processed and sent to courier)
    pending_event.status = "courier_booked"
    pending_event.portal_state = "processing"
    pending_event.is_confirmed = True
    
    db.add(courier_order)
    db.add(pending_event)
    await db.commit()
    
    return {
        "success": True,
        "message": f"Order successfully sent to {req.courier_provider.capitalize()}.",
        "tracking_id": result.get("tracking_id"),
        "courier_order_id": result.get("courier_order_id")
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
        )
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
        password=password
    )
    return stores

