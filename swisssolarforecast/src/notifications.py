"""
Notification module for sending alerts via Telegram.

Configure via environment variables or config file:
- TELEGRAM_BOT_TOKEN: Bot token from @BotFather
- TELEGRAM_CHAT_ID: Chat/group ID to send messages to
"""

import os
import logging
import requests
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Try to load from secrets file if env vars not set
SECRETS_FILE = Path("/home/energymanagement/Documents/secrets.txt")


def _load_secrets():
    """Load Telegram credentials from secrets file."""
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        return  # Already configured via env vars

    if not SECRETS_FILE.exists():
        return

    try:
        with open(SECRETS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                if key == "TELEGRAM_BOT_TOKEN" and not TELEGRAM_BOT_TOKEN:
                    TELEGRAM_BOT_TOKEN = value
                elif key == "TELEGRAM_CHAT_ID" and not TELEGRAM_CHAT_ID:
                    TELEGRAM_CHAT_ID = value
    except Exception as e:
        logger.debug(f"Could not load secrets: {e}")


def is_configured() -> bool:
    """Check if Telegram notifications are configured."""
    _load_secrets()
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(
    message: str,
    parse_mode: str = "HTML",
    disable_notification: bool = False,
) -> bool:
    """
    Send a message via Telegram.

    Args:
        message: Text message to send (supports HTML formatting)
        parse_mode: "HTML" or "Markdown"
        disable_notification: If True, send silently

    Returns:
        True if message was sent successfully
    """
    _load_secrets()

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured, skipping notification")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_notification": disable_notification,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.debug(f"Telegram message sent successfully")
        return True
    except requests.RequestException as e:
        logger.warning(f"Failed to send Telegram message: {e}")
        return False


def notify_warning(title: str, message: str, silent: bool = True) -> bool:
    """
    Send a warning notification.

    Args:
        title: Short title for the warning
        message: Detailed message
        silent: If True, don't trigger notification sound
    """
    text = f"<b>Warning: {title}</b>\n\n{message}"
    return send_telegram(text, disable_notification=silent)


def notify_error(title: str, message: str) -> bool:
    """
    Send an error notification (with sound).

    Args:
        title: Short title for the error
        message: Detailed message
    """
    text = f"<b>Error: {title}</b>\n\n{message}"
    return send_telegram(text, disable_notification=False)


def notify_info(title: str, message: str, silent: bool = True) -> bool:
    """
    Send an info notification.

    Args:
        title: Short title
        message: Detailed message
        silent: If True, don't trigger notification sound
    """
    text = f"<b>{title}</b>\n\n{message}"
    return send_telegram(text, disable_notification=silent)
