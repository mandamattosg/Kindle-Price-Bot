import logging
import os
import re
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from notifications import send_admin_alert

from database import (
    acquire_notification_worker_lock,
    claim_pending_notifications,
    count_pending_notifications,
    create_tables,
    mark_notification_failed,
    mark_notification_sent,
    requeue_notification,
    release_notification_worker_lock,
    reset_stuck_sending_notifications,
    upsert_user_product_state,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


def _extract_retry_after_seconds(response):
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        params = payload.get("parameters") or {}
        retry_after = params.get("retry_after")
        if retry_after is not None:
            return int(retry_after)

        description = str(payload.get("description") or "")
        match = re.search(r"retry after\s+(\d+)", description.lower())
        if match:
            return int(match.group(1))

    text = (response.text or "").lower()
    match = re.search(r"retry after\s+(\d+)", text)
    if match:
        return int(match.group(1))

    return None


def _build_keyboard(button_url):
    if not button_url:
        return None

    return {
        "inline_keyboard": [
            [
                {
                    "text": "📲 Ver oferta na Amazon",
                    "url": button_url,
                    "style": "primary"
                }
            ]
        ]
    }


def _send_queue_item(item):
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN nao configurado")

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": str(item.telegram_id),
        "text": item.message,
    }

    keyboard = _build_keyboard(item.button_url)
    if keyboard is not None:
        payload["reply_markup"] = keyboard

    response = requests.post(url, json=payload, timeout=15)

    if response.status_code == 200:
        return "sent", None

    if response.status_code == 429:
        retry_after = _extract_retry_after_seconds(response) or 3
        return "retry", retry_after

    body = (response.text or "").lower()
    if response.status_code == 403 and "bot was blocked by the user" in body:
        return "blocked", None

    return "error", f"HTTP {response.status_code}: {response.text[:500]}"


def _process_item(item, max_retries):
    try:
        outcome, details = _send_queue_item(item)
    except Exception as exc:
        if item.retry_count + 1 >= max_retries:
            mark_notification_failed(item.id, str(exc)[:500])
            return "failed"

        delay = min(60, 2 ** min(item.retry_count + 1, 5))
        requeue_notification(item.id, delay, str(exc)[:500])
        return "requeued"

    if outcome == "sent":
        mark_notification_sent(item.id)
        if item.new_price is not None:
            upsert_user_product_state(
                item.user_id,
                item.product_id,
                last_seen_price=item.new_price,
                last_notified_price=item.new_price,
                pending_alert_old_price=None,
                pending_alert_new_price=None,
                pending_alert_prime=None,
            )
        return "sent"

    if outcome == "retry":
        delay_seconds = int(details) if details is not None else 3
        requeue_notification(item.id, max(delay_seconds, 1), "flood_wait")
        return "requeued"

    if outcome == "blocked":
        mark_notification_failed(item.id, "user_blocked_bot")
        return "blocked"

    if item.retry_count + 1 >= max_retries:
        mark_notification_failed(item.id, str(details)[:500] if details else "send_failed")
        return "failed"

    delay = min(60, 2 ** min(item.retry_count + 1, 5))
    requeue_notification(item.id, delay, str(details)[:500] if details else "send_failed")
    return "requeued"


def _send_cycle_summary(cycle_started_at, stats, total_items):
    summary_enabled = os.getenv("NOTIFICATION_WORKER_ADMIN_SUMMARY", "1").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not summary_enabled:
        return

    duration_seconds = max(0, int((datetime.now() - cycle_started_at).total_seconds()))
    completed = stats["sent"] + stats["failed"] + stats["blocked"]
    success_rate = (stats["sent"] / total_items * 100) if total_items else 0
    failure_rate = (stats["failed"] / total_items * 100) if total_items else 0
    completion_rate = (completed / total_items * 100) if total_items else 0

    message = (
        "📬 Resumo do worker de notificacoes\n\n"
        f"Tempo de drenagem: {duration_seconds}s\n"
        f"Progresso: {completed}/{total_items} ({completion_rate:.1f}%)\n"
        f"✅ Enviadas: {stats['sent']} ({success_rate:.1f}%)\n"
        f"🔁 Tentativas reenfileiradas: {stats['requeued']}\n"
        f"⛔ Falha definitiva: {stats['failed']} ({failure_rate:.1f}%)\n"
        f"🚫 Usuario bloqueou bot: {stats['blocked']}"
    )

    send_admin_alert(message)


def run_worker_forever():
    create_tables()

    lock_conn = acquire_notification_worker_lock()
    if lock_conn is None:
        logging.warning("Outro notification worker ja esta ativo. Encerrando este processo.")
        return

    batch_size = int(os.getenv("NOTIFICATION_WORKER_BATCH_SIZE", "15"))
    poll_interval = float(os.getenv("NOTIFICATION_WORKER_POLL_INTERVAL", "1.5"))
    batch_sleep = float(os.getenv("NOTIFICATION_WORKER_BATCH_SLEEP", "0.6"))
    max_retries = int(os.getenv("NOTIFICATION_WORKER_MAX_RETRIES", "5"))

    recovered = reset_stuck_sending_notifications()
    if recovered > 0:
        logging.warning("Worker recuperou %s item(ns) presos em sending.", recovered)

    logging.info(
        "Notification worker iniciado (batch_size=%s, poll_interval=%.2fs)",
        batch_size,
        poll_interval,
    )

    cycle_started_at = None
    cycle_item_ids = set()
    cycle_stats = {
        "sent": 0,
        "requeued": 0,
        "failed": 0,
        "blocked": 0,
    }
    had_activity_in_cycle = False

    try:
        while True:
            items = claim_pending_notifications(batch_size=batch_size)

            if not items:
                if had_activity_in_cycle and cycle_started_at is not None:
                    _send_cycle_summary(
                        cycle_started_at,
                        cycle_stats,
                        len(cycle_item_ids),
                    )
                    cycle_started_at = None
                    cycle_item_ids = set()
                    cycle_stats = {
                        "sent": 0,
                        "requeued": 0,
                        "failed": 0,
                        "blocked": 0,
                    }
                    had_activity_in_cycle = False

                time.sleep(max(poll_interval, 0.2))
                continue

            if cycle_started_at is None:
                cycle_started_at = datetime.now()

            for item in items:
                cycle_item_ids.add(item.id)
                result = _process_item(item, max_retries=max_retries)
                if result in cycle_stats:
                    cycle_stats[result] += 1
                had_activity_in_cycle = True

            cycle_total = len(cycle_item_ids)
            total_processed = sum(cycle_stats.values())
            completed = (
                cycle_stats["sent"]
                + cycle_stats["failed"]
                + cycle_stats["blocked"]
            )
            success_rate = cycle_stats["sent"] / cycle_total * 100
            failure_rate = cycle_stats["failed"] / cycle_total * 100
            completion_rate = completed / cycle_total * 100
            pending_after_batch = count_pending_notifications()

            logging.info(
                "Progresso: concluido=%s/%s (%.1f%%) | tentativas=%s | "
                "enviadas=%s (%.1f%%) | falhas=%s (%.1f%%) | "
                "bloqueados=%s | reenfileiradas=%s | pendentes_prontas=%s",
                completed,
                cycle_total,
                completion_rate,
                total_processed,
                cycle_stats["sent"],
                success_rate,
                cycle_stats["failed"],
                failure_rate,
                cycle_stats["blocked"],
                cycle_stats["requeued"],
                pending_after_batch,
            )

            time.sleep(max(batch_sleep, 0.0))
    finally:
        release_notification_worker_lock(lock_conn)


if __name__ == "__main__":
    run_worker_forever()
