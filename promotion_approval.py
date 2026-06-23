import logging
import os
from decimal import Decimal

import requests

from database import (
    create_alert_approval_request,
    get_alert_approval_request,
    get_latest_alert_approval_status_by_signature,
    get_user_product_states_by_product,
    upsert_user_product_state,
)
from alerts import send_price_alert


MONEY_QUANTUM = Decimal("0.01")


def is_promotion_approval_enabled():
    raw_value = os.getenv("ENABLE_PROMOTION_APPROVAL", "1")
    normalized = raw_value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def to_money(value):
    return Decimal(str(value)).quantize(MONEY_QUANTUM)


def _build_admin_confirmation_message(product_name, old_price, new_price, prime=False):
    prime_tag = " (Prime)" if prime else ""
    return (
        f"Promoção no Produto {product_name}{prime_tag}.\n"
        f"De: R$ {to_money(old_price):.2f} Por: R$ {to_money(new_price):.2f}.\n\n"
        "Confirmar envio de alerta?"
    )


def _send_approval_prompt_to_admin(approval_id, message_text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    admin_chat_id = os.getenv("ADMIN_CHAT_ID")

    if not token or not admin_chat_id:
        logging.error(
            "Aprovacao de promocao ativada, mas TELEGRAM_BOT_TOKEN ou ADMIN_CHAT_ID ausente."
        )
        return False

    keyboard = {
        "inline_keyboard": [[
            {
                "text": "Sim",
                "callback_data": f"approval_yes_{approval_id}",
            },
            {
                "text": "Não",
                "callback_data": f"approval_no_{approval_id}",
            },
        ]]
    }

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": admin_chat_id,
                "text": message_text,
                "reply_markup": keyboard,
            },
            timeout=15,
        )
        if response.status_code != 200:
            logging.error(
                "Falha ao enviar pedido de aprovacao ao admin: HTTP %s | %s",
                response.status_code,
                response.text,
            )
            return False
    except Exception:
        logging.exception("Erro ao enviar pedido de aprovacao ao admin")
        return False

    return True


def request_promotion_approval(product_id, product_name, old_price, new_price, prime=False):
    """
    Solicita aprovacao ao admin antes de enviar alertas de promocao.

    Return:
      - True: envio permitido (ja aprovado)
      - False: envio bloqueado (pendente ou rejeitado)
    """
    if not is_promotion_approval_enabled():
        return True

    normalized_old_price = to_money(old_price)
    normalized_new_price = to_money(new_price)

    latest_status = get_latest_alert_approval_status_by_signature(
        product_id=product_id,
        old_price=normalized_old_price,
        new_price=normalized_new_price,
        is_prime=prime,
    )

    if latest_status == "approved":
        return True

    if latest_status in {"pending", "rejected"}:
        return False

    approval_id = create_alert_approval_request(
        product_id=product_id,
        product_name=product_name,
        old_price=normalized_old_price,
        new_price=normalized_new_price,
        is_prime=prime,
    )

    message_text = _build_admin_confirmation_message(
        product_name=product_name,
        old_price=old_price,
        new_price=new_price,
        prime=prime,
    )

    prompt_sent = _send_approval_prompt_to_admin(approval_id, message_text)
    if not prompt_sent:
        return False

    # Sem timeout/polling: aguarda decisao do admin e segue no proximo ciclo.
    return False


def dispatch_approved_promotion_alerts(approval_id):
    """
    Envia imediatamente os alertas pendentes de uma aprovacao ja aprovada.

    Retorna:
      (sent_count, pending_count)
    """
    approval_request = get_alert_approval_request(approval_id)
    if approval_request is None:
        return 0, 0

    (
        _id,
        product_id,
        _product_name,
        _old_price,
        approved_new_price,
        approved_is_prime,
        status,
        _requested_at,
        _decided_at,
        _decided_by,
        _source,
    ) = approval_request

    if status != "approved":
        return 0, 0

    expected_new_price = to_money(approved_new_price)
    expected_prime_flag = bool(approved_is_prime)

    subscribers = get_user_product_states_by_product(product_id)
    sent_count = 0
    pending_count = 0

    for (
        user_id,
        telegram_id,
        _last_seen_price,
        _last_notified_price,
        pending_alert_old_price,
        pending_alert_new_price,
        pending_alert_prime,
    ) in subscribers:
        has_pending_payload = (
            pending_alert_old_price is not None
            and pending_alert_new_price is not None
        )

        if not has_pending_payload:
            continue

        pending_new = to_money(pending_alert_new_price)
        pending_is_prime = bool(pending_alert_prime)

        # Evita disparar payloads pendentes de outros eventos.
        if pending_new != expected_new_price or pending_is_prime != expected_prime_flag:
            pending_count += 1
            continue

        try:
            alert_sent = send_price_alert(
                product_id,
                to_money(pending_alert_old_price),
                pending_new,
                prime=pending_is_prime,
                target_users=[(user_id, telegram_id)],
            )

            if alert_sent:
                sent_count += 1
                upsert_user_product_state(
                    user_id,
                    product_id,
                    last_seen_price=pending_new,
                    last_notified_price=pending_new,
                    pending_alert_old_price=None,
                    pending_alert_new_price=None,
                    pending_alert_prime=None,
                )
            else:
                pending_count += 1

        except Exception:
            logging.exception(
                "Falha no envio imediato de alerta aprovado (approval_id=%s, user_id=%s)",
                approval_id,
                user_id,
            )
            pending_count += 1

    return sent_count, pending_count
