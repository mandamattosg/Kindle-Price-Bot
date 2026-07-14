import logging
from decimal import Decimal

from alerts import build_affiliate_link
from database import enqueue_notification, get_product_by_id

MONEY_QUANTUM = Decimal("0.01")


def to_money(value):
    if value is None:
        return None
    return Decimal(str(value)).quantize(MONEY_QUANTUM)


def build_price_alert_message(product_name, old_price, new_price, prime=False):
    prime_tag = " (Oferta Prime)" if prime else ""
    return (
        "🚨 KINDLE EM PROMOÇÃO AGORA!!\n\n"
        f"📖 {product_name}\n\n"
        f"💸 De: R$ {to_money(old_price):.2f}\n"
        f"🔥 Por: R$ {to_money(new_price):.2f}{prime_tag}\n\n"
        "O preço pode subir novamente a qualquer momento."
    )


def queue_price_alert(product_id, old_price, new_price, prime=False, target_users=None):
    product = get_product_by_id(product_id)

    if not product:
        logging.error("Produto %s nao encontrado para enfileirar alerta", product_id)
        return 0

    asin = product[1]
    product_name = product[2]

    if "Básico" in product_name and product[3]:
        product_name += f" — {product[3]}"

    users = target_users or []
    if not users:
        return 0

    message = build_price_alert_message(
        product_name=product_name,
        old_price=old_price,
        new_price=new_price,
        prime=prime,
    )
    affiliate_link = build_affiliate_link(asin)

    queued = 0

    for user in users:
        if isinstance(user, tuple):
            if len(user) > 1:
                user_id = user[0]
            else:
                user_id = user[0]
        else:
            user_id = user

        queue_id = enqueue_notification(
            user_id=user_id,
            product_id=product_id,
            message=message,
            button_url=affiliate_link,
            old_price=to_money(old_price),
            new_price=to_money(new_price),
            is_prime=bool(prime),
        )

        if queue_id is not None:
            queued += 1

    return queued
