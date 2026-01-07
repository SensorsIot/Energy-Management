"""
Notification module for sending alerts via Telegram.

Configure via user config file or environment variables:
- telegram.bot_token: Bot token from @BotFather
- telegram.chat_id: Chat/group ID to send messages to
"""

import os
import logging
import requests
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration (set via init_telegram())
_BOT_TOKEN: str = ""
_CHAT_ID: str = ""


def init_telegram(bot_token: str = "", chat_id: str = ""):
    """
    Initialize Telegram credentials from config.

    Args:
        bot_token: Telegram bot token
        chat_id: Telegram chat ID
    """
    global _BOT_TOKEN, _CHAT_ID

    # Use provided values or fall back to environment variables
    _BOT_TOKEN = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    _CHAT_ID = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    if _BOT_TOKEN and _CHAT_ID:
        logger.info("Telegram notifications configured")
    else:
        logger.debug("Telegram notifications not configured")


def is_configured() -> bool:
    """Check if Telegram notifications are configured."""
    return bool(_BOT_TOKEN and _CHAT_ID)


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
    if not _BOT_TOKEN or not _CHAT_ID:
        logger.debug("Telegram not configured, skipping notification")
        return False

    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": _CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_notification": disable_notification,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.debug("Telegram message sent successfully")
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
