"""Shared PII normalization and hashing helpers."""

import hashlib
import re


def normalize_bd_phone(raw: str | None) -> str:
    digits = re.sub(r"[^0-9]", "", raw or "")
    if len(digits) == 11 and digits.startswith("01"):
        return "88" + digits
    if len(digits) == 10 and digits.startswith("1"):
        return "880" + digits
    if digits.startswith("880"):
        return digits
    if digits.startswith("0"):
        return digits.lstrip("0")
    return digits


def hash_pii(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if re.fullmatch(r"[a-f0-9]{64}", text):
        return text
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_phone(raw: str | None) -> str:
    return hash_pii(normalize_bd_phone(raw))
