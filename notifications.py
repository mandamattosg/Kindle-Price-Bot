import requests
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")


def send_admin_alert(message: str):

    if not ADMIN_CHAT_ID:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": ADMIN_CHAT_ID,
                "text": message
            },
            timeout=15
        )

    except Exception as e:
        print(f"Falha ao enviar alerta admin: {e}")

