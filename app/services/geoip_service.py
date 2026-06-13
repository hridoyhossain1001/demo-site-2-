import os
import logging
import hashlib
import httpx
import ipaddress
import maxminddb
import tarfile
import tempfile
import time
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DB_URL = "https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb"
DB_PATH = os.getenv("GEOIP_DB_PATH", "GeoLite2-City.mmdb")
MAXMIND_EDITION_ID = os.getenv("MAXMIND_EDITION_ID", "GeoLite2-City")
MAXMIND_ACCOUNT_ID = os.getenv("MAXMIND_ACCOUNT_ID", "")
MAXMIND_LICENSE_KEY = os.getenv("MAXMIND_LICENSE_KEY", "")
GEOIP_MAX_AGE_DAYS = int(os.getenv("GEOIP_MAX_AGE_DAYS", "7"))
ALLOW_GEOIP_FALLBACK_MIRROR = os.getenv("ALLOW_GEOIP_FALLBACK_MIRROR", "false").lower() in {"1", "true", "yes"}
GEOIP_FALLBACK_SHA256 = os.getenv("GEOIP_FALLBACK_SHA256", "").strip().lower()

_reader = None


def _ensure_reader_loaded():
    global _reader
    if _reader or not os.path.exists(DB_PATH):
        return
    try:
        _reader = maxminddb.open_database(DB_PATH)
        logger.info("GeoIP database lazy-loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to lazy-load GeoIP database: {e}")


def _global_ip_or_none(ip_address: str):
    try:
        ip = ipaddress.ip_address(str(ip_address).strip())
    except ValueError:
        return None
    if not ip.is_global:
        return None
    return str(ip)


def _db_is_stale() -> bool:
    path = Path(DB_PATH)
    if not path.exists():
        return True
    if GEOIP_MAX_AGE_DAYS <= 0:
        return False
    max_age_seconds = GEOIP_MAX_AGE_DAYS * 86400
    return (path.stat().st_mtime + max_age_seconds) < time.time()


async def _download_official_maxmind_db(tmp_path: str):
    if not MAXMIND_ACCOUNT_ID or not MAXMIND_LICENSE_KEY:
        raise RuntimeError("MaxMind account id or license key is not configured.")

    url = f"https://download.maxmind.com/geoip/databases/{MAXMIND_EDITION_ID}/download?suffix=tar.gz"
    timeout = httpx.Timeout(240.0, connect=30.0)
    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_path = os.path.join(tmp_dir, f"{MAXMIND_EDITION_ID}.tar.gz")
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            async with client.stream(
                "GET",
                url,
                auth=(MAXMIND_ACCOUNT_ID, MAXMIND_LICENSE_KEY),
            ) as response:
                response.raise_for_status()
                with open(archive_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)

        with tarfile.open(archive_path, "r:gz") as archive:
            mmdb_member = next(
                (
                    member
                    for member in archive.getmembers()
                    if member.isfile() and member.name.endswith(".mmdb")
                ),
                None,
            )
            if not mmdb_member:
                raise RuntimeError("MaxMind archive did not contain an .mmdb file.")
            extracted = archive.extractfile(mmdb_member)
            if extracted is None:
                raise RuntimeError("Could not extract MaxMind .mmdb file.")
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = extracted.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)


async def _download_fallback_geoip_db(tmp_path: str):
    if len(GEOIP_FALLBACK_SHA256) != 64 or any(char not in "0123456789abcdef" for char in GEOIP_FALLBACK_SHA256):
        raise RuntimeError("GEOIP_FALLBACK_SHA256 must be a pinned 64-character SHA-256 digest.")

    timeout = httpx.Timeout(180.0, connect=20.0)
    digest = hashlib.sha256()
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async with client.stream("GET", DB_URL) as response:
            response.raise_for_status()
            with open(tmp_path, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)
                    digest.update(chunk)
    if digest.hexdigest() != GEOIP_FALLBACK_SHA256:
        raise RuntimeError("Fallback GeoIP database SHA-256 did not match GEOIP_FALLBACK_SHA256.")


async def download_geoip_db_if_missing():
    """Download or refresh the GeoLite2 City database, then load the reader."""
    global _reader
    if _db_is_stale():
        has_official_credentials = bool(MAXMIND_ACCOUNT_ID and MAXMIND_LICENSE_KEY)
        if not has_official_credentials and not ALLOW_GEOIP_FALLBACK_MIRROR:
            logger.error(
                "GeoIP database missing/stale and MaxMind credentials are not configured. "
                "Set MAXMIND_ACCOUNT_ID/MAXMIND_LICENSE_KEY or explicitly enable "
                "ALLOW_GEOIP_FALLBACK_MIRROR=true to use the third-party mirror."
            )
            if not os.path.exists(DB_PATH):
                return
            logger.warning("Using existing stale GeoIP database; refresh skipped.")
        else:
            source = "official MaxMind" if has_official_credentials else "fallback mirror"
            logger.info(f"Refreshing GeoIP database from {source} (this may take a minute)...")
            tmp_path = f"{DB_PATH}.part"
            try:
                if has_official_credentials:
                    await _download_official_maxmind_db(tmp_path)
                else:
                    logger.warning("Using third-party GeoIP mirror because ALLOW_GEOIP_FALLBACK_MIRROR=true.")
                    await _download_fallback_geoip_db(tmp_path)
                os.replace(tmp_path, DB_PATH)
                _lookup_location_data.cache_clear()
                logger.info("GeoIP database refreshed successfully.")
            except Exception as e:
                logger.error(f"Failed to download GeoIP database: {e}")
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass
                if not os.path.exists(DB_PATH):
                    return

    # Initialize reader
    try:
        if _reader:
            try:
                _reader.close()
            except Exception:
                pass
            _reader = None
        _reader = maxminddb.open_database(DB_PATH)
        logger.info("GeoIP database loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load GeoIP database: {e}")

@lru_cache(maxsize=int(os.getenv("GEOIP_CACHE_SIZE", "10000")))
def _lookup_location_data(ip_address: str) -> tuple[tuple[str, str], ...]:
    if not _reader:
        return ()
    try:
        data = _reader.get(ip_address)
    except Exception as e:
        logger.warning(f"GeoIP lookup failed for IP {ip_address}: {e}")
        return ()

    if not data:
        return ()

    loc = {}

    # City
    if 'city' in data and 'names' in data['city'] and 'en' in data['city']['names']:
        loc['ct'] = data['city']['names']['en']

    # State/Region
    if 'subdivisions' in data and len(data['subdivisions']) > 0:
        sub = data['subdivisions'][0]
        if 'iso_code' in sub:
            loc['st'] = sub['iso_code']
        elif 'names' in sub and 'en' in sub['names']:
            loc['st'] = sub['names']['en']

    # Country
    if 'country' in data and 'iso_code' in data['country']:
        loc['country'] = data['country']['iso_code'].lower()

    # Zip Code
    if 'postal' in data and 'code' in data['postal']:
        loc['zp'] = data['postal']['code']

    return tuple(loc.items())


def get_location_data(ip_address: str) -> dict:
    """
    Get city, state, country, and zip code from IP address.
    Returns a dict with 'ct', 'st', 'country', 'zp' keys.
    """
    if not ip_address:
        return {}

    _ensure_reader_loaded()

    global_ip = _global_ip_or_none(ip_address)
    if not global_ip:
        return {}

    return dict(_lookup_location_data(global_ip))

def close_geoip_db():
    global _reader
    if _reader:
        try:
            _reader.close()
        except Exception:
            pass
        _reader = None
    _lookup_location_data.cache_clear()
