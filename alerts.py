import logging

import requests
import os
from dotenv import load_dotenv

from database import get_users_by_product, get_product_by_id
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AFILIADO_TAG = os.getenv("AMAZON_ASSOCIATE_TAG")


def build_affiliate_link(asin: str) -> str:
    return f"https://www.amazon.com.br/dp/{asin}?tag={AFILIADO_TAG}"


def send_price_alert(product_id, old_price, new_price, prime=False, target_users=None):

    product = get_product_by_id(product_id)

    if not product:
        print(f"❌ Produto {product_id} não encontrado")
        return

    asin = product[1]
    product_name = product[2]
    if "Básico" in product_name and product[3]:  # cor
        product_name += f" — {product[3]}"

    affiliate_link = build_affiliate_link(asin)

    if target_users is None:
        users = get_users_by_product(product_id)
    else:
        users = target_users

    prime_tag = " (Oferta Prime)" if prime else ""

    message = (
        f"🚨 KINDLE EM PROMOÇÃO AGORA!!\n\n"
        f"📖 {product_name}\n\n"
        f"💸 De: R$ {old_price:.2f}\n"
        f"🔥 Por: R$ {new_price:.2f}{prime_tag}\n\n"
        f"O preço pode subir novamente a qualquer momento."
    )

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    # 🔥 botão inline
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "📲 Ver oferta na Amazon",
                    "url": affiliate_link,
                    "style": "primary"
                }
            ]
        ]
    }

    success = True

    for user in users:

        if isinstance(user, tuple):
            if len(user) > 1:
                telegram_id = user[1]
            else:
                telegram_id = user[0]
        else:
            telegram_id = user

        try:
            response = requests.post(
                url,
                json={
                    "chat_id": str(telegram_id),
                    "text": message,
                    "reply_markup": keyboard
                },
                timeout=15
            )

            if response.status_code != 200:
                print(f"❌ Telegram erro {response.status_code}: {response.text}")
                success = False
            else:
                logging.info(
                    f"Alerta enviado para {telegram_id}"
                )

        except Exception as e:
            print(f"❌ Erro enviando alerta: {e}")
            success = False

    return success