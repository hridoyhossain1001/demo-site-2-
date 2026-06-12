import logging
import os
import re
import time
from typing import Dict, Any, Optional, Tuple
from app.services.capi_service import get_http_client

logger = logging.getLogger(__name__)

PATHAO_BASE_URL = os.getenv("PATHAO_BASE_URL", "https://api-hermes.pathao.com").rstrip("/")
PATHAO_SANDBOX_BASE_URL = "https://courier-api-sandbox.pathao.com"
STEADFAST_BASE_URL = "https://portal.packzy.com"
REDX_BASE_URL = "https://openapi.redx.com.bd/v1.0.0-beta"

# ─── Pathao Token Cache ────────────────────────────────────────────────────────
# Pathao OAuth2 tokens সাধারণত 1 ঘণ্টা valid থাকে। প্রতি request-এ নতুন token
# নেওয়ার বদলে 50 মিনিট cache করে রাখি — latency কমে + Pathao API rate limit এড়ায়।
_PATHAO_TOKEN_TTL = 50 * 60  # 50 minutes (safe margin before 1-hour expiry)
_pathao_token_cache: dict[str, tuple[str, float]] = {}  # cache_key -> (token, expires_at)

# ─── Pathao Location Cache ────────────────────────────────────────────────────
# City/Zone list প্রতি deployment-এ একবার fetch করলেই চলে — 6 ঘণ্টা TTL।
_PATHAO_LOCATION_CACHE_TTL = 6 * 60 * 60  # 6 hours
_pathao_city_cache: dict[str, tuple[list, float]] = {}   # token_prefix -> (cities, expires_at)
_pathao_zone_cache: dict[str, tuple[list, float]] = {}   # f"{token_prefix}:{city_id}" -> (zones, expires_at)

# ─── Bangladesh District → Common Aliases ─────────────────────────────────────
# Pathao API-তে city মানে district। এই mapping address string থেকে
# district বের করতে সাহায্য করে — ইংরেজি ও বাংলা উভয় নামেই।
_BD_DISTRICT_ALIASES: dict[str, str] = {
    # Dhaka Division
    "dhaka": "Dhaka", "ঢাকা": "Dhaka",
    "gazipur": "Gazipur", "গাজীপুর": "Gazipur",
    "narayanganj": "Narayanganj", "নারায়ণগঞ্জ": "Narayanganj",
    "narsingdi": "Narsingdi", "নরসিংদী": "Narsingdi",
    "manikganj": "Manikganj", "মানিকগঞ্জ": "Manikganj",
    "munshiganj": "Munshiganj", "মুন্সিগঞ্জ": "Munshiganj",
    "kishoreganj": "Kishoreganj", "কিশোরগঞ্জ": "Kishoreganj",
    "tangail": "Tangail", "টাঙ্গাইল": "Tangail",
    "faridpur": "Faridpur", "ফরিদপুর": "Faridpur",
    "madaripur": "Madaripur", "মাদারীপুর": "Madaripur",
    "shariatpur": "Shariatpur", "শরীয়তপুর": "Shariatpur",
    "rajbari": "Rajbari", "রাজবাড়ী": "Rajbari",
    "gopalganj": "Gopalganj", "গোপালগঞ্জ": "Gopalganj",
    # Chittagong Division
    "chittagong": "Chattogram", "chattogram": "Chattogram", "চট্টগ্রাম": "Chattogram",
    "cox's bazar": "Cox's Bazar", "coxs bazar": "Cox's Bazar", "কক্সবাজার": "Cox's Bazar",
    "comilla": "Cumilla", "cumilla": "Cumilla", "কুমিল্লা": "Cumilla",
    "feni": "Feni", "ফেনী": "Feni",
    "lakshmipur": "Lakshmipur", "লক্ষ্মীপুর": "Lakshmipur",
    "noakhali": "Noakhali", "নোয়াখালী": "Noakhali",
    "chandpur": "Chandpur", "চাঁদপুর": "Chandpur",
    "brahmanbaria": "Brahmanbaria", "ব্রাহ্মণবাড়িয়া": "Brahmanbaria",
    "rangamati": "Rangamati", "রাঙ্গামাটি": "Rangamati",
    "khagrachhari": "Khagrachhari", "খাগড়াছড়ি": "Khagrachhari",
    "bandarban": "Bandarban", "বান্দরবান": "Bandarban",
    # Sylhet Division
    "sylhet": "Sylhet", "সিলেট": "Sylhet",
    "moulvibazar": "Moulvibazar", "মৌলভীবাজার": "Moulvibazar",
    "habiganj": "Habiganj", "হবিগঞ্জ": "Habiganj",
    "sunamganj": "Sunamganj", "সুনামগঞ্জ": "Sunamganj",
    # Rajshahi Division
    "rajshahi": "Rajshahi", "রাজশাহী": "Rajshahi",
    "chapainawabganj": "Chapai Nawabganj", "নওগাঁ": "Naogaon", "naogaon": "Naogaon",
    "natore": "Natore", "নাটোর": "Natore",
    "bogra": "Bogura", "bogura": "Bogura", "বগুড়া": "Bogura",
    "joypurhat": "Joypurhat", "জয়পুরহাট": "Joypurhat",
    "pabna": "Pabna", "পাবনা": "Pabna",
    "sirajganj": "Sirajganj", "সিরাজগঞ্জ": "Sirajganj",
    # Khulna Division
    "khulna": "Khulna", "খুলনা": "Khulna",
    "jessore": "Jashore", "jashore": "Jashore", "যশোর": "Jashore",
    "satkhira": "Satkhira", "সাতক্ষীরা": "Satkhira",
    "bagerhat": "Bagerhat", "বাগেরহাট": "Bagerhat",
    "narail": "Narail", "নড়াইল": "Narail",
    "magura": "Magura", "মাগুরা": "Magura",
    "jhenaidah": "Jhenaidah", "ঝিনাইদহ": "Jhenaidah",
    "kushtia": "Kushtia", "কুষ্টিয়া": "Kushtia",
    "chuadanga": "Chuadanga", "চুয়াডাঙ্গা": "Chuadanga",
    "meherpur": "Meherpur", "মেহেরপুর": "Meherpur",
    # Barisal Division
    "barisal": "Barishal", "barishal": "Barishal", "বরিশাল": "Barishal",
    "patuakhali": "Patuakhali", "পটুয়াখালী": "Patuakhali",
    "barguna": "Barguna", "বরগুনা": "Barguna",
    "pirojpur": "Pirojpur", "পিরোজপুর": "Pirojpur",
    "jhalokati": "Jhalokati", "ঝালকাঠি": "Jhalokati",
    "bhola": "Bhola", "ভোলা": "Bhola",
    # Rangpur Division
    "rangpur": "Rangpur", "রংপুর": "Rangpur",
    "dinajpur": "Dinajpur", "দিনাজপুর": "Dinajpur",
    "thakurgaon": "Thakurgaon", "ঠাকুরগাঁও": "Thakurgaon",
    "panchagarh": "Panchagarh", "পঞ্চগড়": "Panchagarh",
    "nilphamari": "Nilphamari", "নীলফামারী": "Nilphamari",
    "lalmonirhat": "Lalmonirhat", "লালমনিরহাট": "Lalmonirhat",
    "kurigram": "Kurigram", "কুড়িগ্রাম": "Kurigram",
    "gaibandha": "Gaibandha", "গাইবান্ধা": "Gaibandha",
    # Mymensingh Division
    "mymensingh": "Mymensingh", "ময়মনসিংহ": "Mymensingh",
    "netrokona": "Netrokona", "নেত্রকোনা": "Netrokona",
    "jamalpur": "Jamalpur", "জামালপুর": "Jamalpur",
    "sherpur": "Sherpur", "শেরপুর": "Sherpur",
}


class CourierServiceException(Exception):
    pass


class CourierService:

    @staticmethod
    def pathao_base_url(environment: str | None = None) -> str:
        if str(environment or "").strip().lower() == "sandbox":
            return PATHAO_SANDBOX_BASE_URL
        return PATHAO_BASE_URL

    @staticmethod
    def pathao_is_sandbox(base_url: str) -> bool:
        return base_url.rstrip("/") == PATHAO_SANDBOX_BASE_URL.rstrip("/")

    @staticmethod
    def normalize_bd_phone(phone: str) -> str:
        """Normalize Bangladeshi phone numbers to Pathao's local 01XXXXXXXXX format."""
        digits = re.sub(r"\D+", "", str(phone or ""))
        if digits.startswith("880") and len(digits) == 13:
            return "0" + digits[3:]
        if digits.startswith("88") and len(digits) == 13:
            return digits[2:]
        if digits.startswith("1") and len(digits) == 10:
            return "0" + digits
        return digits

    # ─── Pathao Auth ──────────────────────────────────────────────────────────

    @staticmethod
    async def get_pathao_token(
        client_id: str, client_secret: str, store_owner_email: str, password: str,
        base_url: str | None = None,
    ) -> Optional[str]:
        """Pathao API Token সংগ্রহ করার জন্য OAuth2 Call। 50-minute TTL cache সহ।"""
        base_url = CourierService.pathao_base_url() if not base_url else base_url.rstrip("/")
        cache_key = f"{base_url}:{client_id}:{store_owner_email}"
        now = time.monotonic()

        if cache_key in _pathao_token_cache:
            cached_token, expires_at = _pathao_token_cache[cache_key]
            if now < expires_at:
                return cached_token

        url = f"{base_url}/aladdin/api/v1/issue-token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "username": store_owner_email,
            "password": password,
            "grant_type": "password",
        }
        headers = {"accept": "application/json", "content-type": "application/json"}

        http = await get_http_client()
        try:
            response = await http.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                token = data.get("access_token")
                if token:
                    _pathao_token_cache[cache_key] = (token, now + _PATHAO_TOKEN_TTL)
                return token
            else:
                logger.error(
                    f"Pathao Token generation failed: {response.status_code} - {response.text}"
                )
                _pathao_token_cache.pop(cache_key, None)
                return None
        except Exception as e:
            logger.error(f"Exception during Pathao Token request: {e}")
            return None

    # ─── Pathao Location APIs (City / Zone / Area) ────────────────────────────

    @staticmethod
    async def _get_pathao_cities(token: str, base_url: str | None = None) -> list:
        """
        Pathao-র সব City (= District) fetch করে। 6-ঘণ্টা TTL cache-এ রাখে।
        Return: [{"city_id": 1, "city_name": "Dhaka"}, ...]
        """
        cache_key = token[:16]  # token prefix দিয়ে key
        now = time.monotonic()
        if cache_key in _pathao_city_cache:
            cities, expires_at = _pathao_city_cache[cache_key]
            if now < expires_at:
                return cities

        base_url = CourierService.pathao_base_url() if not base_url else base_url.rstrip("/")
        city_list_path = "countries/1/city-list" if CourierService.pathao_is_sandbox(base_url) else "city-list"
        url = f"{base_url}/aladdin/api/v1/{city_list_path}"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        http = await get_http_client()
        try:
            resp = await http.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                cities = data.get("data", {}).get("data", []) or []
                _pathao_city_cache[cache_key] = (cities, now + _PATHAO_LOCATION_CACHE_TTL)
                logger.info(f"Pathao: fetched {len(cities)} cities")
                return cities
            else:
                logger.warning(f"Pathao city-list failed: {resp.status_code}")
                return []
        except Exception as e:
            logger.error(f"Pathao city-list exception: {e}")
            return []

    @staticmethod
    async def _get_pathao_zones(token: str, city_id: int, base_url: str | None = None) -> list:
        """
        একটি city-র সব Zone fetch করে। cache সহ।
        Return: [{"zone_id": 1, "zone_name": "Mirpur"}, ...]
        """
        cache_key = f"{token[:16]}:{city_id}"
        now = time.monotonic()
        if cache_key in _pathao_zone_cache:
            zones, expires_at = _pathao_zone_cache[cache_key]
            if now < expires_at:
                return zones

        base_url = CourierService.pathao_base_url() if not base_url else base_url.rstrip("/")
        url = f"{base_url}/aladdin/api/v1/cities/{city_id}/zone-list"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        http = await get_http_client()
        try:
            resp = await http.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                zones = data.get("data", {}).get("data", []) or []
                _pathao_zone_cache[cache_key] = (zones, now + _PATHAO_LOCATION_CACHE_TTL)
                return zones
            else:
                logger.warning(f"Pathao zone-list failed for city {city_id}: {resp.status_code}")
                return []
        except Exception as e:
            logger.error(f"Pathao zone-list exception: {e}")
            return []

    @staticmethod
    async def _get_pathao_areas(token: str, zone_id: int, base_url: str | None = None) -> list:
        """
        একটি zone-র সব Area fetch করে।
        Return: [{"area_id": 1, "area_name": "Mirpur-1"}, ...]
        """
        base_url = CourierService.pathao_base_url() if not base_url else base_url.rstrip("/")
        url = f"{base_url}/aladdin/api/v1/zones/{zone_id}/area-list"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        http = await get_http_client()
        try:
            resp = await http.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("data", []) or []
            return []
        except Exception as e:
            logger.error(f"Pathao area-list exception: {e}")
            return []

    # ─── Address → City/Zone/Area Resolution ─────────────────────────────────

    @staticmethod
    def _extract_district_from_address(address: str) -> str:
        """
        Address string থেকে Bangladesh district নাম বের করে।
        Strategy:
          1. alias map-এ exact word match
          2. address-এর শেষ কয়েকটা token-এ match (city usually শেষে থাকে)
        """
        if not address:
            return ""

        address_lower = address.lower().strip()

        # Comma দিয়ে split করে শেষের দিক থেকে চেক (city সাধারণত শেষে থাকে)
        parts = [p.strip() for p in re.split(r"[,،،\n]", address_lower) if p.strip()]
        # শেষ থেকে শুরু করে match করি
        for part in reversed(parts):
            part_clean = part.strip().rstrip(".")
            # exact match
            if part_clean in _BD_DISTRICT_ALIASES:
                return _BD_DISTRICT_ALIASES[part_clean]
            # partial match — part যদি কোনো alias contain করে
            for alias, canonical in _BD_DISTRICT_ALIASES.items():
                if len(alias) >= 4 and alias in part_clean:
                    return canonical

        # Fallback: পুরো address-এ word-by-word scan
        words = re.split(r"[\s,،،\-]+", address_lower)
        for word in reversed(words):
            word = word.strip(".")
            if word in _BD_DISTRICT_ALIASES:
                return _BD_DISTRICT_ALIASES[word]

        return ""

    @staticmethod
    def _fuzzy_match(name: str, candidates: list, name_key: str) -> Optional[dict]:
        """
        name-কে candidates list-এর name_key field-এর সাথে fuzzy match করে।
        Priority: exact > startswith > contains
        """
        name_lower = name.lower().strip()
        if not name_lower or not candidates:
            return None

        for item in candidates:
            item_name = str(item.get(name_key, "")).lower().strip()
            if item_name == name_lower:
                return item  # exact match

        for item in candidates:
            item_name = str(item.get(name_key, "")).lower().strip()
            if item_name.startswith(name_lower) or name_lower.startswith(item_name):
                return item  # prefix match

        for item in candidates:
            item_name = str(item.get(name_key, "")).lower().strip()
            if name_lower in item_name or item_name in name_lower:
                return item  # contains match

        return None

    @classmethod
    async def resolve_pathao_location(
        cls,
        token: str,
        recipient_address: str,
        base_url: str | None = None,
    ) -> Tuple[int, int, int]:
        """
        Address থেকে Pathao city_id / zone_id / area_id resolve করে।

        Logic:
          1. Address থেকে district extract করো
          2. Pathao city list থেকে match করো → city_id
          3. City-র প্রথম zone নাও → zone_id (default)
          4. Zone-র প্রথম area নাও → area_id (default)
          5. Match না হলে Dhaka fallback (city=1, zone=1, area=1)

        Returns: (city_id, zone_id, area_id)
        """
        DHAKA_FALLBACK = (1, 1, 1)

        district = cls._extract_district_from_address(recipient_address)
        if not district:
            logger.warning(
                f"Could not extract district from address: '{recipient_address[:80]}'. "
                "Falling back to Dhaka."
            )
            return DHAKA_FALLBACK

        # ── Step 1: City match ────────────────────────────────────────────────
        cities = await cls._get_pathao_cities(token, base_url=base_url)
        if not cities:
            logger.warning("Pathao city list empty — falling back to Dhaka")
            return DHAKA_FALLBACK

        matched_city = cls._fuzzy_match(district, cities, "city_name")
        if not matched_city:
            logger.warning(
                f"Pathao: district '{district}' not found in city list — "
                f"falling back to Dhaka. Address: '{recipient_address[:80]}'"
            )
            return DHAKA_FALLBACK

        city_id = int(matched_city["city_id"])
        logger.info(
            f"Pathao location resolved: district='{district}' → "
            f"city='{matched_city['city_name']}' (id={city_id})"
        )

        # ── Step 2: Zone — city-র প্রথম zone নাও ────────────────────────────
        zones = await cls._get_pathao_zones(token, city_id, base_url=base_url)
        if not zones:
            logger.warning(f"No zones for Pathao city_id={city_id} — using zone_id=1")
            return (city_id, 1, 1)

        zone_id = int(zones[0]["zone_id"])

        # ── Step 3: Area — zone-র প্রথম area নাও ────────────────────────────
        areas = await cls._get_pathao_areas(token, zone_id, base_url=base_url)
        if not areas:
            logger.warning(f"No areas for Pathao zone_id={zone_id} — using area_id=1")
            return (city_id, zone_id, 1)

        area_id = int(areas[0]["area_id"])

        logger.info(
            f"Pathao IDs resolved → city_id={city_id}, zone_id={zone_id}, area_id={area_id}"
        )
        return (city_id, zone_id, area_id)

    # ─── Send Order to Pathao ─────────────────────────────────────────────────

    @classmethod
    async def get_pathao_stores(
        cls,
        client_id: str,
        client_secret: str,
        email: str,
        password: str,
        base_url: str | None = None,
    ) -> list:
        """
        Pathao Merchant-এর সব registered store fetch করে।
        API: GET /aladdin/api/v1/stores
        """
        base_url = cls.pathao_base_url() if not base_url else base_url.rstrip("/")
        token = await cls.get_pathao_token(client_id, client_secret, email, password, base_url=base_url)
        if not token:
            logger.error("Failed to authenticate with Pathao API for store fetching.")
            return []

        url = f"{base_url}/aladdin/api/v1/stores"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        http = await get_http_client()
        try:
            resp = await http.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                store_data = data.get("data", {})
                if isinstance(store_data, dict):
                    return store_data.get("data", []) or []
                elif isinstance(store_data, list):
                    return store_data
                return []
            else:
                logger.warning(f"Pathao store-list failed: {resp.status_code} - {resp.text}")
                return []
        except Exception as e:
            logger.error(f"Pathao store-list exception: {e}")
            return []

    @classmethod
    async def send_to_pathao(
        cls,
        client_id: str,
        client_secret: str,
        email: str,
        password: str,
        store_id: str,
        recipient_name: str,
        recipient_phone: str,
        recipient_address: str,
        cod_amount: float,
        merchant_order_id: str,
        item_quantity: int = 1,
        item_weight: float = 0.5,
        item_description: Optional[str] = None,
        recipient_city: Optional[int] = None,
        recipient_zone: Optional[int] = None,
        recipient_area: Optional[int] = None,
        base_url: str | None = None,
    ) -> Dict[str, Any]:
        """
        Pathao Courier-এ অর্ডার প্লেস করা।
        Address থেকে city/zone/area dynamically resolve করে — সারা বাংলাদেশ support।
        """
        recipient_phone = cls.normalize_bd_phone(recipient_phone)
        base_url = cls.pathao_base_url() if not base_url else base_url.rstrip("/")
        token = await cls.get_pathao_token(client_id, client_secret, email, password, base_url=base_url)
        if not token:
            raise CourierServiceException("Failed to authenticate with Pathao API.")

        # ── Dynamic location resolution ───────────────────────────────────────
        url = f"{base_url}/aladdin/api/v1/orders"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Truncate description if too long
        desc_to_use = item_description or f"Order {merchant_order_id}"
        if len(desc_to_use) > 300:
            desc_to_use = desc_to_use[:297] + "..."

        payload = {
            "store_id": int(store_id),
            "merchant_order_id": merchant_order_id,
            "recipient_name": recipient_name,
            "recipient_phone": recipient_phone,
            "recipient_address": recipient_address,
            "delivery_type": 48,  # Normal Delivery (48 Hours)
            "item_type": 2,       # Parcel
            "item_quantity": item_quantity,
            "item_weight": item_weight,
            "amount_to_collect": cod_amount,
            "item_description": desc_to_use,
        }

        explicit_location_ids = all((recipient_city, recipient_zone, recipient_area))
        if explicit_location_ids or cls.pathao_is_sandbox(base_url):
            if explicit_location_ids:
                city_id, zone_id, area_id = recipient_city, recipient_zone, recipient_area
            else:
                city_id, zone_id, area_id = await cls.resolve_pathao_location(
                    token, recipient_address, base_url=base_url
                )
            payload.update(
                recipient_city=city_id,
                recipient_zone=zone_id,
                recipient_area=area_id,
            )

        logger.info(
            "Pathao order payload: order=%s environment=%s explicit_location_ids=%s",
            merchant_order_id,
            "sandbox" if cls.pathao_is_sandbox(base_url) else "live",
            explicit_location_ids or cls.pathao_is_sandbox(base_url),
        )

        http = await get_http_client()
        try:
            response = await http.post(url, json=payload, headers=headers)
            if response.status_code in (200, 201):
                data = response.json()
                order_data = data.get("data", {})
                consignment_id = order_data.get("consignment_id")
                if not consignment_id:
                    logger.error(f"Pathao order placement returned no consignment ID: {data}")
                    return {
                        "success": False,
                        "error": "Pathao response did not include a consignment ID",
                        "raw_response": data,
                    }
                return {
                    "success": True,
                    "courier_order_id": str(consignment_id),
                    "tracking_id": str(consignment_id),
                    "raw_response": data,
                }
            else:
                logger.error(
                    f"Pathao order placement failed: {response.status_code} - {response.text}"
                )
                return {"success": False, "error": response.text}
        except Exception as e:
            logger.error(f"Exception during Pathao order: {e}")
            return {"success": False, "error": str(e)}

    # ─── Send Order to SteadFast ──────────────────────────────────────────────

    @classmethod
    async def send_to_steadfast(
        cls,
        api_key: str,
        secret_key: str,
        recipient_name: str,
        recipient_phone: str,
        recipient_address: str,
        cod_amount: float,
        merchant_order_id: str,
    ) -> Dict[str, Any]:
        """
        SteadFast Courier-এ অর্ডার প্লেস করা।
        SteadFast-এ city/zone/area নেই — address free-text।
        """
        recipient_phone = cls.normalize_bd_phone(recipient_phone)
        url = f"{STEADFAST_BASE_URL}/api/v1/create_order"
        headers = {
            "Api-Key": api_key,
            "Secret-Key": secret_key,
            "Content-Type": "application/json",
        }

        payload = {
            "invoice": merchant_order_id,
            "recipient_name": recipient_name,
            "recipient_phone": recipient_phone,
            "recipient_address": recipient_address,
            "cod_amount": cod_amount,
            "note": f"Order {merchant_order_id}",
        }

        http = await get_http_client()
        try:
            response = await http.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                status = data.get("status")
                if status == 200:
                    order_data = data.get("consignment", {})
                    consignment_id = order_data.get("consignment_id")
                    tracking_code = order_data.get("tracking_code")
                    if not consignment_id or not tracking_code:
                        logger.error(f"SteadFast order placement returned incomplete IDs: {data}")
                        return {
                            "success": False,
                            "error": "SteadFast response did not include consignment and tracking IDs",
                            "raw_response": data,
                        }
                    return {
                        "success": True,
                        "courier_order_id": str(consignment_id),
                        "tracking_id": str(tracking_code),
                        "raw_response": data,
                    }
                else:
                    return {
                        "success": False,
                        "error": data.get("message", "Unknown SteadFast API error"),
                    }
            else:
                logger.error(
                    f"SteadFast order placement failed: {response.status_code} - {response.text}"
                )
                return {"success": False, "error": response.text}
        except Exception as e:
            logger.error(f"Exception during SteadFast order: {e}")
            return {"success": False, "error": str(e)}

    # ─── Status Check: SteadFast ──────────────────────────────────────────────

    @classmethod
    async def check_steadfast_status(
        cls, api_key: str, secret_key: str, tracking_code: str
    ) -> Optional[str]:
        """SteadFast-এর ট্র্যাক কোড দিয়ে স্ট্যাটাস চেক করা।"""
        url = f"{STEADFAST_BASE_URL}/api/v1/status_by_trackingcode/{tracking_code}"
        headers = {
            "Api-Key": api_key,
            "Secret-Key": secret_key,
            "Content-Type": "application/json",
        }

        http = await get_http_client()
        try:
            response = await http.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return data.get("delivery_status")
            return None
        except Exception as e:
            logger.error(f"Failed to check SteadFast status: {e}")
            return None

    # ─── Status Check: Pathao ─────────────────────────────────────────────────

    @classmethod
    async def check_pathao_status(
        cls,
        client_id: str,
        client_secret: str,
        email: str,
        password: str,
        consignment_id: str,
        base_url: str | None = None,
    ) -> Optional[str]:
        """Pathao Consignment ID দিয়ে স্ট্যাটাস চেক করা।"""
        base_url = cls.pathao_base_url() if not base_url else base_url.rstrip("/")
        token = await cls.get_pathao_token(client_id, client_secret, email, password, base_url=base_url)
        if not token:
            return None

        url = f"{base_url}/aladdin/api/v1/orders/{consignment_id}/info"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        http = await get_http_client()
        try:
            response = await http.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                order_data = data.get("data", {})
                return (
                    order_data.get("order_status_slug")
                    or order_data.get("order_status")
                    or order_data.get("status")
                )
            return None
        except Exception as e:
            logger.error(f"Failed to check Pathao status: {e}")
            return None

    # --- RedX ---

    @classmethod
    async def send_to_redx(
        cls,
        access_token: str,
        recipient_name: str,
        recipient_phone: str,
        recipient_address: str,
        cod_amount: float,
        merchant_order_id: str,
        delivery_area_id: str,
        delivery_area_name: str,
        pickup_store_id: Optional[str] = None,
        item_weight: float = 0.5,
        declared_value: Optional[float] = None,
    ) -> Dict[str, Any]:
        url = f"{REDX_BASE_URL}/parcel"
        headers = {
            "API-ACCESS-TOKEN": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "customer_name": recipient_name,
            "customer_phone": cls.normalize_bd_phone(recipient_phone),
            "delivery_area": delivery_area_name,
            "delivery_area_id": int(delivery_area_id),
            "customer_address": recipient_address,
            "merchant_invoice_id": merchant_order_id,
            "cash_collection_amount": str(cod_amount),
            "parcel_weight": int(float(item_weight) * 1000),
            "instruction": f"Order {merchant_order_id}",
            "value": declared_value if declared_value is not None else cod_amount,
        }
        if pickup_store_id:
            payload["pickup_store_id"] = int(pickup_store_id)

        http = await get_http_client()
        try:
            response = await http.post(url, json=payload, headers=headers)
            if response.status_code in (200, 201):
                data = response.json()
                tracking_id = data.get("tracking_id")
                if tracking_id:
                    return {
                        "success": True,
                        "courier_order_id": str(tracking_id),
                        "tracking_id": str(tracking_id),
                        "raw_response": data,
                    }
            logger.error(f"RedX parcel creation failed: {response.status_code} - {response.text}")
            return {"success": False, "error": response.text}
        except Exception as e:
            logger.error(f"Exception during RedX parcel creation: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    async def check_redx_status(cls, access_token: str, tracking_id: str) -> Optional[str]:
        url = f"{REDX_BASE_URL}/parcel/info/{tracking_id}"
        headers = {"API-ACCESS-TOKEN": f"Bearer {access_token}"}
        http = await get_http_client()
        try:
            response = await http.get(url, headers=headers)
            if response.status_code == 200:
                return response.json().get("parcel", {}).get("status")
            return None
        except Exception as e:
            logger.error(f"Failed to check RedX status: {e}")
            return None

    @classmethod
    async def get_redx_areas(cls, access_token: str) -> list:
        url = f"{REDX_BASE_URL}/areas"
        headers = {"API-ACCESS-TOKEN": f"Bearer {access_token}"}
        http = await get_http_client()
        try:
            response = await http.get(url, headers=headers)
            if response.status_code == 200:
                return response.json().get("areas", []) or []
            logger.error(f"RedX areas request failed: {response.status_code} - {response.text}")
            return []
        except Exception as e:
            logger.error(f"Failed to fetch RedX areas: {e}")
            return []

    @classmethod
    async def cancel_redx_order(cls, access_token: str, tracking_id: str) -> Dict[str, Any]:
        url = f"{REDX_BASE_URL}/parcels"
        headers = {
            "API-ACCESS-TOKEN": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "entity_type": "parcel-tracking-id",
            "entity_id": tracking_id,
            "update_details": {
                "property_name": "status",
                "new_value": "cancelled",
                "reason": "Cancelled by merchant",
            },
        }
        http = await get_http_client()
        try:
            response = await http.patch(url, json=payload, headers=headers)
            if response.status_code in (200, 201, 202):
                data = response.json()
                if data.get("success"):
                    return {"success": True, "local_only": False, "message": data.get("message", "RedX cancellation accepted.")}
            return {"success": False, "error": response.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ─── Cancel Order: Pathao ─────────────────────────────────────────────────

    @classmethod
    async def cancel_pathao_order(
        cls,
        client_id: str,
        client_secret: str,
        email: str,
        password: str,
        consignment_id: str,
        base_url: str | None = None,
    ) -> Dict[str, Any]:
        """
        Pathao-তে Pending/Pickable অর্ডার cancel করা।
        Pathao API: POST /aladdin/api/v1/orders/cancel
        Body: { "consignment_id": <integer> }
        শুধুমাত্র 'Pending' বা 'Pickup Requested' state-এর order cancel করা যায়।

        গুরুত্বপূর্ণ: Pathao কখনো HTTP 200 দিলেও body-তে error থাকতে পারে।
        body-র 'code' field চেক করতে হয়।
        """
        base_url = cls.pathao_base_url() if not base_url else base_url.rstrip("/")
        token = await cls.get_pathao_token(client_id, client_secret, email, password, base_url=base_url)
        if not token:
            return {
                "success": True,
                "local_only": True,
                "message": "Pathao authentication failed. Order marked cancelled locally. Please cancel manually from Pathao merchant panel.",
            }

        url = f"{base_url}/aladdin/api/v1/orders/cancel"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Pathao-র API consignment_id integer হিসেবে expect করে
        try:
            consignment_id_val = int(consignment_id)
        except (TypeError, ValueError):
            consignment_id_val = consignment_id

        payload = {"consignment_id": consignment_id_val}

        http = await get_http_client()
        try:
            response = await http.post(url, json=payload, headers=headers)

            # Response body parse করা
            try:
                data = response.json()
            except Exception:
                data = {}

            # Pathao response field extraction
            # Pathao sometimes returns: {"error": true, "success": true, "message": "Unauthorized!"}
            # We must check "error": true as a failure signal too.
            body_code = data.get("code") or data.get("status")
            body_error_flag = data.get("error")  # True = failed, False/None = ok
            body_message = data.get("message") or ""
            if not body_message and isinstance(body_error_flag, str):
                body_message = body_error_flag  # fallback if error is a string message

            if response.status_code in (200, 201):
                # Pathao কখনো HTTP 200 দিয়েও body-তে error signal দিতে পারে।
                # Case 1: body-তে numeric error code (>= 400)
                try:
                    body_code_int = int(body_code) if body_code is not None else 200
                except (TypeError, ValueError):
                    body_code_int = 200

                # Case 2: body-তে "error": true (Unauthorized বা অন্য error)
                pathao_error = body_code_int >= 400 or body_error_flag is True

                if pathao_error:
                    logger.error(
                        f"Pathao cancel HTTP 200 but error detected "
                        f"(code={body_code_int}, error_flag={body_error_flag}) "
                        f"for consignment {consignment_id}: {body_message}"
                    )
                    reason = body_message or f"Pathao error (code={body_code_int})"
                    return {
                        "success": True,
                        "local_only": True,
                        "message": (
                            f"Pathao-তে cancel করা সম্ভব হয়নি: {reason} "
                            "(শুধু এই system-এ cancelled হয়েছে। "
                            "Pathao merchant panel থেকে manually cancel করুন।)"
                        ),
                    }

                logger.info(f"Pathao order {consignment_id} cancelled successfully: {data}")
                return {
                    "success": True,
                    "local_only": False,
                    "message": f"Pathao-তে order সফলভাবে cancel হয়েছে। {body_message}".strip(". ") + ".",
                    "raw_response": data,
                }
            else:
                err_text = response.text
                logger.error(
                    f"Pathao cancel failed for {consignment_id}: "
                    f"{response.status_code} - {err_text}"
                )
                err_msg = body_message or err_text
                # HTTP error — local_only দিই, DB-তে cancelled করব কিন্তু user-কে জানাব
                return {
                    "success": True,
                    "local_only": True,
                    "message": (
                        f"Pathao API error ({response.status_code}): {err_msg}. "
                        "(শুধু এই system-এ cancelled হয়েছে। "
                        "Pathao merchant panel থেকে manually cancel করুন।)"
                    ),
                }
        except Exception as e:
            logger.error(f"Exception during Pathao cancel for {consignment_id}: {e}")
            return {
                "success": True,
                "local_only": True,
                "message": (
                    f"Network error: {e}. "
                    "(শুধু এই system-এ cancelled হয়েছে। "
                    "Pathao merchant panel থেকে manually cancel করুন।)"
                ),
            }

    @classmethod
    async def cancel_steadfast_order(
        cls,
        api_key: str,
        secret_key: str,
        tracking_code: str,
    ) -> Dict[str, Any]:
        """
        SteadFast Courier-এ order cancel।
        SteadFast-এর public API-তে cancel endpoint নেই।
        শুধু local DB update হবে, courier-side cancel-এর জন্য
        SteadFast merchant panel-এ manually করতে হবে।
        """
        logger.info(
            f"SteadFast does not support API-level cancel. "
            f"Tracking {tracking_code} marked cancelled locally only."
        )
        return {
            "success": True,
            "local_only": True,
            "message": (
                "SteadFast does not support API cancellation. "
                "Order has been marked as cancelled locally. "
                "Please cancel manually from the SteadFast merchant panel if needed."
            ),
        }

    # ─── Status Mapper ────────────────────────────────────────────────────────

    _STATUS_MAPS = {
        "steadfast": {
            "pending": "pending",
            "hold": "pending",
            "in-review": "pending",
            "unknown": "pending",
            "unknown-approval-pending": "pending",
            "delivered": "delivered",
            "delivered-approval-pending": "in_transit",
            "partial-delivered": "partial_delivered",
            "partial-delivered-approval-pending": "in_transit",
            "completed": "delivered",
            "returned": "returned",
            "partial-returned": "returned",
            "cancelled": "cancelled",
            "cancelled-approval-pending": "in_transit",
            "canceled": "cancelled",
            "in-transit": "in_transit",
            "picked-up": "in_transit",
            "shipped": "in_transit",
        },
        "pathao": {
            "pending": "pending",
            "delivered": "delivered",
            "returned": "returned",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "picked": "in_transit",
            "picked-up": "in_transit",
            "in-transit": "in_transit",
            "shipped": "in_transit",
        },
        "redx": {
            "pending": "pending",
            "pickup-pending": "pending",
            "delivered": "delivered",
            "returned": "returned",
            "agent-returning": "returned",
            "partial-return": "returned",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "rejected": "cancelled",
            "ready-for-delivery": "in_transit",
            "delivery-in-progress": "in_transit",
            "agent-hold": "in_transit",
            "agent-area-change": "in_transit",
        },
    }

    @staticmethod
    def normalize_status_token(raw_status: str) -> str:
        """Normalize courier spelling variants before provider-specific mapping."""
        return "-".join(str(raw_status or "").strip().lower().replace("_", "-").split())

    @classmethod
    def is_known_status(cls, provider: str, raw_status: str) -> bool:
        provider_key = str(provider or "").strip().lower()
        return cls.normalize_status_token(raw_status) in cls._STATUS_MAPS.get(provider_key, {})

    @classmethod
    def map_status(cls, provider: str, raw_status: str) -> str:
        """Map provider-specific statuses to the internal courier lifecycle."""
        provider_key = str(provider or "").strip().lower()
        status_token = cls.normalize_status_token(raw_status)
        return cls._STATUS_MAPS.get(provider_key, {}).get(status_token, "pending")

    @staticmethod
    def should_apply_status_transition(old_status: str, new_status: str) -> bool:
        """Reject stale callbacks that would move a shipment backwards."""
        old_status = str(old_status or "pending").strip().lower()
        new_status = str(new_status or "pending").strip().lower()
        if old_status == new_status:
            return False
        if old_status in ("returned", "cancelled", "partial_delivered"):
            return False
        if old_status == "delivered":
            return new_status in ("returned", "cancelled")
        if old_status == "in_transit" and new_status == "pending":
            return False
        return True
