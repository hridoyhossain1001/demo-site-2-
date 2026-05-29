import logging
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Request, HTTPException
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
    # Retrieve pending event
    event_result = await db.execute(
        select(PendingEvent).where(
            (PendingEvent.id == req.pending_event_id) & (PendingEvent.client_id == client.id)
        )
    )
    pending_event = event_result.scalar_one_or_none()
    if not pending_event:
        raise HTTPException(status_code=404, detail="Order (pending event) not found.")
        
    # Check if this order is already sent
    order_exist = await db.execute(
        select(CourierOrder).where(
            (CourierOrder.client_id == client.id) & (CourierOrder.order_id == pending_event.order_id)
        )
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
        if not client.pathao_api_key or not client.pathao_secret_key or not client.pathao_store_id:
            raise HTTPException(status_code=400, detail="Pathao API credentials are not configured.")
            
        try:
            client_id, email = client.pathao_api_key.split("|", 1)
            decrypted_secret_pass = decrypt_token(client.pathao_secret_key)
            client_secret, password = decrypted_secret_pass.split("|", 1)
        except ValueError:
            raise HTTPException(
                status_code=400, 
                detail="Pathao credentials format incorrect. Expected client_id|email and client_secret|password."
            )
            
        result = await CourierService.send_to_pathao(
            client_id=client_id,
            client_secret=client_secret,
            email=email,
            password=password,
            store_id=client.pathao_store_id,
            recipient_name=req.recipient_name,
            recipient_phone=req.recipient_phone,
            recipient_address=req.recipient_address,
            cod_amount=req.cod_amount,
            merchant_order_id=pending_event.order_id
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
async def get_courier_orders(client: Client = Depends(get_current_portal_client), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CourierOrder).where(CourierOrder.client_id == client.id).order_by(desc(CourierOrder.created_at))
    )
    orders = result.scalars().all()
    
    response = []
    for order in orders:
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
            purchase_event_sent=order.purchase_event_sent
        ))
    return response
