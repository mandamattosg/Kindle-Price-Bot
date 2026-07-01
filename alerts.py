import logging
import time
import requests
import os
from dotenv import load_dotenv

from database import (
    get_users_by_product,
    get_product_by_id,
    remove_all_monitorings_by_telegram_id
)
from notifications import send_admin_alert

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
        send_admin_alert(
            "🚨 Produto inexistente em envio de alerta\n\n"
            f"product_id: {product_id}\n"
            "Falha ao montar mensagem de alerta porque o produto não foi encontrado no banco."
        )
        return False

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
    max_attempts = 2
    retry_delay_seconds = 2
    blocked_users_count = 0
    blocked_removed_monitorings = 0

    for user in users:

        if isinstance(user, tuple):
            if len(user) > 1:
                telegram_id = user[1]
            else:
                telegram_id = user[0]
        else:
            telegram_id = user

        delivered = False
        last_error = None
        blocked_by_user = False

        for attempt in range(1, max_attempts + 1):
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

                if response.status_code == 200:
                    logging.info(f"Alerta enviado para {telegram_id}")
                    delivered = True
                    break

                if (
                    response.status_code == 403
                    and "bot was blocked by the user" in response.text.lower()
                ):
                    blocked_by_user = True
                    removed_count = remove_all_monitorings_by_telegram_id(telegram_id)
                    blocked_users_count += 1
                    blocked_removed_monitorings += removed_count
                    last_error = "HTTP 403: bot was blocked by the user"
                    logging.info(
                        "Usuário %s bloqueou o bot. Monitoramentos removidos: %s",
                        telegram_id,
                        removed_count
                    )
                    break

                last_error = f"HTTP {response.status_code}: {response.text}"
                logging.warning(
                    f"Falha ao enviar alerta para {telegram_id} "
                    f"(tentativa {attempt}/{max_attempts}): {last_error}"
                )

            except Exception as e:
                last_error = str(e)
                logging.warning(
                    f"Erro ao enviar alerta para {telegram_id} "
                    f"(tentativa {attempt}/{max_attempts}): {last_error}"
                )

            if attempt < max_attempts:
                time.sleep(retry_delay_seconds)

        if not delivered:
            success = False

            if blocked_by_user:
                continue

            alert_type = "Prime" if prime else "Public"
            send_admin_alert(
                f"⚠️ Warning envio Telegram\n\n"
                f"Usuário: {telegram_id}\n"
                f"Produto: {product_name}\n"
                f"Tipo: {alert_type}\n"
                f"Tentativas: {max_attempts}\n"
                f"Erro final: {(last_error or 'desconhecido')[:500]}"
            )

    if blocked_users_count > 0:
        alert_type = "Prime" if prime else "Public"
        send_admin_alert(
            "ℹ️ Resumo de usuários bloqueados\n\n"
            f"Produto: {product_name}\n"
            f"Tipo: {alert_type}\n"
            f"Usuários bloqueados no ciclo: {blocked_users_count}\n"
            f"Monitoramentos removidos: {blocked_removed_monitorings}"
        )

    return success