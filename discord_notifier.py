import os
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

ERROR_WEBHOOK = os.getenv("DISCORD_ERROR_WEBHOOK")
STATUS_WEBHOOK = os.getenv("DISCORD_STATUS_WEBHOOK")

def send_discord_alert(webhook_url: str, title: str, description: str, color: int):
    """Sends a formatted embed to a specific Discord webhook."""
    if not webhook_url:
        return

    payload = {
        "username": "Quant System Monitor",
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "Trading Engine GCP"}
            }
        ]
    }
    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def notify_error(error_message: str):
    """Routes critical system crashes or API rejections directly to #bot-errors."""
    send_discord_alert(
        webhook_url=ERROR_WEBHOOK,
        title="🚨 Critical System Error",
        description=f"```python\n{error_message}\n```",
        color=15158332  # Red
    )

def notify_system_status(status_text: str, buying_power: float):
    """Routes successful Alpaca connection checks to #system-status."""
    send_discord_alert(
        webhook_url=STATUS_WEBHOOK,
        title="✅ System Heartbeat / Status",
        description=f"**Status:** {status_text}\n**Buying Power:** ${buying_power:,.2f}",
        color=3066993  # Green
    )