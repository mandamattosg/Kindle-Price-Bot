import logging
import os
from decimal import Decimal

import requests

from database import (
    get_or_create_alert_approval_request,
    refresh_pending_alert_approval_request_by_signature,
    set_alert_approval_admin_message_reference,
    get_alert_approval_request,
    get_user_product_states_by_product,
)
from notification_queue import queue_price_alert


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
        f"👀 Promoção no Produto {product_name}{prime_tag}.\n"
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
        return False, None, None

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
            return False, None, None

        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else None
        message_id = None
        chat_id = admin_chat_id

        if isinstance(result, dict):
            message_id = result.get("message_id")
            chat = result.get("chat") or {}
            chat_id = chat.get("id", admin_chat_id)

        return True, chat_id, message_id
    except Exception:
        logging.exception("Erro ao enviar pedido de aprovacao ao admin")
        return False, None, None


def _edit_approval_prompt_to_admin(approval_id, message_text, admin_chat_id, admin_message_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token or admin_chat_id is None or admin_message_id is None:
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
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": str(admin_chat_id),
                "message_id": int(admin_message_id),
                "text": message_text,
                "reply_markup": keyboard,
            },
            timeout=15,
        )

        if response.status_code == 200:
            return True

        body = (response.text or "").lower()
        if "message is not modified" in body:
            return True

        logging.warning(
            "Falha ao editar pedido de aprovacao ao admin: HTTP %s | %s",
            response.status_code,
            response.text,
        )
        return False
    except Exception:
        logging.exception("Erro ao editar pedido de aprovacao ao admin")
        return False


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

    approval_id, latest_status = get_or_create_alert_approval_request(
        product_id=product_id,
        product_name=product_name,
        old_price=normalized_old_price,
        new_price=normalized_new_price,
        is_prime=prime,
    )

    if latest_status == "approved":
        return True

    if latest_status == "pending":
        refreshed = refresh_pending_alert_approval_request_by_signature(
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

        if refreshed is not None:
            refreshed_approval_id, admin_chat_id, admin_message_id = refreshed

            edited = _edit_approval_prompt_to_admin(
                refreshed_approval_id,
                message_text,
                admin_chat_id,
                admin_message_id,
            )

            if not edited:
                sent, sent_chat_id, sent_message_id = _send_approval_prompt_to_admin(
                    refreshed_approval_id,
                    message_text,
                )

                if sent:
                    set_alert_approval_admin_message_reference(
                        refreshed_approval_id,
                        sent_chat_id,
                        sent_message_id,
                    )
        return False

    if latest_status == "rejected":
        return False

    message_text = _build_admin_confirmation_message(
        product_name=product_name,
        old_price=old_price,
        new_price=new_price,
        prime=prime,
    )

    prompt_sent, admin_chat_id, admin_message_id = _send_approval_prompt_to_admin(
        approval_id,
        message_text,
    )
    if not prompt_sent:
        return False

    set_alert_approval_admin_message_reference(
        approval_id,
        admin_chat_id,
        admin_message_id,
    )

    # Sem timeout/polling: aguarda decisao do admin e segue no proximo ciclo.
    return False


def dispatch_approved_promotion_alerts(approval_id):
    """
        Enfileira os alertas pendentes de uma aprovacao ja aprovada.

    Retorna:
            (queued_count, pending_count)
    """
    approval_request = get_alert_approval_request(approval_id)
    if approval_request is None:
        return 0, 0

    (
        _id,
        product_id,
        _product_name,
        approved_old_price,
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
    expected_old_price = to_money(approved_old_price)
    expected_prime_flag = bool(approved_is_prime)

    subscribers = get_user_product_states_by_product(product_id)
    sent_count = 0
    pending_count = 0
    sent_user_ids = set()

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
            queued_count = queue_price_alert(
                product_id,
                to_money(pending_alert_old_price),
                pending_new,
                prime=pending_is_prime,
                target_users=[(user_id, telegram_id)],
            )

            if queued_count > 0:
                sent_count += 1
                sent_user_ids.add(user_id)
            else:
                pending_count += 1

        except Exception:
            logging.exception(
                "Falha no envio imediato de alerta aprovado (approval_id=%s, user_id=%s)",
                approval_id,
                user_id,
            )
            pending_count += 1

    # Fallback para corrida entre clique no "Sim" e persistencia do payload
    # pendente: envia usando os dados da propria aprovacao para inscritos
    # ainda nao notificados neste despacho.
    for (
        user_id,
        telegram_id,
        _last_seen_price,
        last_notified_price,
        _pending_alert_old_price,
        _pending_alert_new_price,
        _pending_alert_prime,
    ) in subscribers:
        if user_id in sent_user_ids:
            continue

        already_notified_for_same_or_better = False
        if last_notified_price is not None:
            try:
                already_notified_for_same_or_better = (
                    to_money(last_notified_price) <= expected_new_price
                )
            except Exception:
                already_notified_for_same_or_better = False

        if already_notified_for_same_or_better:
            continue

        try:
            queued_count = queue_price_alert(
                product_id,
                expected_old_price,
                expected_new_price,
                prime=expected_prime_flag,
                target_users=[(user_id, telegram_id)],
            )

            if queued_count > 0:
                sent_count += 1
            else:
                pending_count += 1

        except Exception:
            logging.exception(
                "Falha no fallback de envio imediato (approval_id=%s, user_id=%s)",
                approval_id,
                user_id,
            )
            pending_count += 1

    return sent_count, pending_count
