"""Lightweight bot and suspicious-traffic classification."""

import logging
import re

logger = logging.getLogger(__name__)

BOT_PATTERNS = [
    "googlebot", "bingbot", "slurp", "duckduckbot", "baiduspider",
    "yandexbot", "sogou", "exabot", "ia_archiver",
    "facebookexternalhit", "facebot", "twitterbot", "linkedinbot",
    "pinterestbot", "telegrambot", "whatsapp", "slackbot", "discordbot",
    "semrushbot", "ahrefsbot", "mj12bot", "dotbot", "rogerbot",
    "screaming frog", "seokicks", "sistrix",
    "bot/", "crawler", "spider", "scraper", "headless",
    "phantomjs", "selenium", "puppeteer", "playwright",
    "wget", "curl/", "python-requests", "python-urllib",
    "go-http-client", "java/", "apache-httpclient",
    "node-fetch", "axios/", "libwww-perl",
    "uptimerobot", "pingdom", "statuscake", "site24x7",
    "newrelicpinger", "datadog",
]

_BOT_REGEX = re.compile("|".join(re.escape(p) for p in BOT_PATTERNS), re.IGNORECASE)


def classify_traffic(
    user_agent: str | None,
    *,
    ip: str | None = None,
    has_cookie: bool = False,
) -> str:
    """Return bot, suspicious, or human from lightweight traffic signals."""
    ua = (user_agent or "").strip()
    if _BOT_REGEX.search(ua):
        logger.info(f"Bot detected: {ua[:80]}...")
        return "bot"
    if len(ua) < 10:
        if has_cookie:
            logger.info("Suspicious traffic: empty/short User-Agent with first-party cookie.")
            return "suspicious"
        logger.info("Bot detected: empty/short User-Agent without first-party cookie.")
        return "bot"
    return "human"


def is_bot(user_agent: str | None) -> bool:
    """Backward-compatible boolean bot check for callers that still hard-drop."""
    return classify_traffic(user_agent) == "bot"
