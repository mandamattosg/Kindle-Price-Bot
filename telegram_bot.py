from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters
)

import httpx
import httpcore

from telegram.error import (
    NetworkError,
    TimedOut
)

import logging
import traceback
from datetime import datetime, date
from notifications import send_admin_alert
from dotenv import load_dotenv
import os


from database import (
    get_active_products,
    save_user,
    get_user_by_telegram_id,
    add_user_product,
    get_user_products,
    get_last_prices,
    get_product_price_history,
    initialize_user_product_state,
    get_active_product_promotion_state,
    deactivate_product_promotion_state,
    set_product_promotion_stop_date,
    get_best_price_info,
    get_report_stats,
    get_users_with_active_monitorings,
    enqueue_coupon_notifications_bulk,
    get_users_who_received_product_alert,
    enqueue_notifications_bulk,
    get_products_lowest_prices,
    get_alert_approval_request,
    resolve_alert_approval_request
)

from alerts import build_affiliate_link
from kindle_scraper import process_product, PriceResult
from notification_queue import queue_price_alert
from promotion_approval import dispatch_approved_promotion_alerts, is_promotion_approval_enabled

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

def ensure_user(update: Update):
    user = update.effective_user

    if user is None:
        logging.warning(
            "ensure_user chamado sem effective_user: %s",
            update.to_dict()
        )
        return

    save_user(
        telegram_id=user.id,
        username=user.username
    )

def is_admin_user(update: Update):
    admin_id = os.getenv("ADMIN_TELEGRAM_ID")

    if not admin_id or update.effective_user is None:
        return False

    return str(update.effective_user.id) == str(admin_id)

async def error_handler(update, context):

    error = context.error

    # Erros temporários de rede: apenas registra no log
    if isinstance(
        error,
        (
            httpx.ReadError,
            httpcore.ReadError,
            TimedOut,
            NetworkError
        )
    ):
        logging.warning("Erro temporário de rede: %s", error)

        send_admin_alert(
            "⚠️ Erro temporário de rede\n"
            #f"{error_text[:3000]}"
        )

        return

    logging.error(
        "❌ Erro no kindle bot",
        exc_info=error
    )

    try:
        error_text = "".join(
            traceback.format_exception(
                type(error),
                error,
                error.__traceback__
            )
        )

        send_admin_alert(
            "🚨 ERRO NO KINDLE BOT\n\n"
            f"{error_text[:3000]}"
        )

    except Exception:
        pass

# =========================
# MENSAGEM PADRÃO
# =========================

WELCOME_MESSAGE = (
    "🤖 Olá! Eu monitoro preços de Kindle na Amazon "
    "e te aviso quando eles baixarem!\n\n"

    "Comandos disponíveis:\n\n"

    "/kindle → escolher um Kindle para monitorar\n"
    "/status → ver seus monitoramentos ativos\n"
    "/remover → parar de monitorar um Kindle\n"
    "/ajuda → mostrar esta mensagem novamente"
)


def get_welcome_message(update: Update):
    message = WELCOME_MESSAGE
    if is_admin_user(update):
        message = message.replace(
            "/ajuda → mostrar esta mensagem novamente",
            "/ajuda → mostrar esta mensagem novamente\n/manual → registrar preços manualmente (admin)\n/relatorio → relatório de uso do bot (admin)\n/cupom → enviar mensagem para usuários ativos (admin)\n/lembrete → reenviar aviso para quem recebeu alerta (admin)\n/resumo → enviar resumo de campanha (admin)\n/encerraroferta → parar alertas dessa promoção a partir de uma data (admin)"
        )
    return message


# =========================
# KEYBOARD DE PRODUTOS
# =========================

def build_products_keyboard(prefix: str = "track_"):

    products = get_active_products()

    keyboard = []

    for product in products:

        product_id, asin, name, color = product

        button_text = name

        # só mostra cor no Kindle básico
        if "Básico" in name and color:
            button_text += f" — {color}"

        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"{prefix}{product_id}"
            )
        ])

    return InlineKeyboardMarkup(keyboard)


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ensure_user(update)

    logging.info(
        f"Novo usuário: {user.id} (@{user.username})"
    )

    await update.message.reply_text(get_welcome_message(update))


# =========================
# AJUDA
# =========================

async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ensure_user(update)

    await update.message.reply_text(get_welcome_message(update))


# =========================
# KINDLE
# =========================

async def kindle(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ensure_user(update)

    reply_markup = build_products_keyboard()

    await update.message.reply_text(
        "📖 Qual Kindle você quer monitorar?",
        reply_markup=reply_markup
    )


# =========================
# RELATORIO (ADMIN)
# =========================

async def relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text("⛔ Comando disponível apenas para o administrador.")
        return

    stats = get_report_stats()

    if not stats:
        await update.message.reply_text("❌ Não foi possível obter as estatísticas.")
        return

    top = f"{stats['top_product']} ({stats['top_product_count']} usuário(s))" if stats["top_product"] else "—"

    msg = (
        "📊 *Relatório do Bot*\n\n"
        f"👥 Total de usuários: *{stats['total_users']}*\n"
        f"🔍 Monitorando ao menos 1 Kindle: *{stats['users_monitoring']}*\n"
        f"🏆 Produto mais monitorado: *{top}*"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


# =========================
# MANUAL (ADMIN)
# =========================

MANUAL_SELECT, MANUAL_PRICES = range(2)

async def manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    ensure_user(update)

    reply_markup = build_products_keyboard(prefix="manual_")

    await update.message.reply_text(
        "🛠️ Selecione o produto para lançar o preço manualmente:",
        reply_markup=reply_markup
    )

    return MANUAL_SELECT


async def manual_select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin_user(update):
        await query.answer("Sem permissão", show_alert=True)
        return ConversationHandler.END

    if query.data is None or not query.data.startswith("manual_"):
        await query.message.reply_text(
            "❌ Seleção inválida. Tente novamente."
        )
        return ConversationHandler.END

    product_id = int(query.data.split("_")[1])
    products = get_active_products()
    selected_product = next((p for p in products if p[0] == product_id), None)

    if not selected_product:
        await query.message.reply_text(
            "❌ Produto não encontrado."
        )
        return ConversationHandler.END

    context.user_data["manual_product"] = selected_product

    await query.message.reply_text(
        "💵 Informe os dois preços em uma só mensagem, por exemplo:\n\n"
        "799\n"
        "749\n\n"
        "Ou use o formato:\n"
        "Preço público: 799\n"
        "Preço Prime: 749"
    )

    return MANUAL_PRICES


def parse_price_text(text: str):
    cleaned = text.strip().lower()
    if cleaned in {"-", "nenhum", "0", "0,00", "0.00"}:
        return None

    cleaned = cleaned.replace("r$", "").replace(" ", "")

    if "," in cleaned:
        cleaned = cleaned.replace(".", "")
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_two_prices(text: str):
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    if len(lines) == 1:
        parts = lines[0]
        parts = parts.replace("preço público:", "")
        parts = parts.replace("preço prime:", "")
        parts = parts.replace("público:", "")
        parts = parts.replace("prime:", "")
        parts = parts.split()
    else:
        parts = lines

    if len(parts) < 2:
        return None, None

    public_line = (
        parts[0]
        .replace("preço público:", "")
        .replace("público:", "")
        .strip()
    )
    prime_line = (
        parts[1]
        .replace("preço prime:", "")
        .replace("prime:", "")
        .strip()
    )

    public_price = parse_price_text(public_line)
    prime_price = parse_price_text(prime_line)

    return public_price, prime_price


async def manual_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text if update.message else ""
    public_price, prime_price = parse_two_prices(text)

    if public_price is None and prime_price is None:
        await update.message.reply_text(
            "❌ Formato inválido. Envie pelo menos um preço válido nas duas posições:\n\n"
            "799\n"
            "749\n\n"
            "Ou use:\n"
            "Preço público: 799\n"
            "Preço Prime: 749\n\n"
            "Use '-' ou 'nenhum' para um preço ausente em qualquer posição."
        )
        return MANUAL_PRICES

    manual_product = context.user_data.get("manual_product")

    if not manual_product:
        await update.message.reply_text(
            "❌ Algo deu errado. Por favor, reinicie o comando /manual."
        )
        return ConversationHandler.END

    public_result = (
        PriceResult(price=public_price, url="manual-public")
        if public_price is not None
        else None
    )
    prime_result = (
        PriceResult(price=prime_price, url="manual-prime")
        if prime_price is not None
        else None
    )

    success = process_product(
        public_context=None,
        prime_context=None,
        product=manual_product,
        manual=True,
        public_result=public_result,
        prime_result=prime_result
    )

    public_text = (
        f"R$ {public_price:.2f}"
        if public_price is not None
        else "nenhum"
    )
    prime_text = (
        f"R$ {prime_price:.2f}"
        if prime_price is not None
        else "nenhum"
    )

    price_status = (
        f"Público: {public_text}\n"
        f"Prime: {prime_text}"
    )

    if success:
        confirmation = (
            "✅ Histórico salvo com sucesso.\n\n"
            f"{price_status}\n\n"
            "O sistema verificou se havia alertas e enviou notificações quando necessário."
        )
    else:
        confirmation = (
            "❌ Não foi possível salvar o histórico.\n\n"
            f"{price_status}\n\n"
            "O produto não foi processado com sucesso."
        )

    await update.message.reply_text(confirmation)

    context.user_data.pop("manual_product", None)

    return ConversationHandler.END


async def manual_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Comando manual cancelado."
    )
    return ConversationHandler.END


# =========================
# CUPOM (ADMIN)
# =========================

COUPON_MESSAGE, COUPON_CONFIRM = range(2, 4)


async def cupom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    ensure_user(update)

    await update.message.reply_text(
        "🧾 Qual a mensagem de cupom você quer enviar?"
    )

    return COUPON_MESSAGE


async def cupom_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    coupon_message = (update.message.text or "").strip()

    if not coupon_message:
        await update.message.reply_text(
            "❌ Mensagem vazia. Envie o texto do cupom."
        )
        return COUPON_MESSAGE

    if len(coupon_message) > 4000:
        await update.message.reply_text(
            "❌ Mensagem muito longa. Envie um texto com até 4000 caracteres."
        )
        return COUPON_MESSAGE

    context.user_data["coupon_message"] = coupon_message

    reply_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Sim", callback_data="coupon_yes"),
            InlineKeyboardButton("❌ Não", callback_data="coupon_no")
        ]
    ])

    await update.message.reply_text(
        "Confirmar envio da mensagem?\n\n"
        f"{coupon_message}",
        reply_markup=reply_markup
    )

    return COUPON_CONFIRM


async def cupom_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin_user(update):
        await query.answer("Sem permissão", show_alert=True)
        return ConversationHandler.END

    coupon_message = context.user_data.get("coupon_message")
    if not coupon_message:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "❌ Mensagem de cupom não encontrada. Reinicie com /cupom."
        )
        return ConversationHandler.END

    if query.data == "coupon_no":
        context.user_data.pop("coupon_message", None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Envio de cupom cancelado.")
        return ConversationHandler.END

    users = get_users_with_active_monitorings()

    if not users:
        context.user_data.pop("coupon_message", None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "ℹ️ Não há usuários com monitoramentos ativos no momento."
        )
        return ConversationHandler.END

    user_ids = [user_id for user_id, _telegram_id in users]

    try:
        queued_count = enqueue_coupon_notifications_bulk(
            user_ids=user_ids,
            message=coupon_message,
        )
        fail_count = max(0, len(user_ids) - queued_count)
    except Exception as exc:
        queued_count = 0
        fail_count = len(user_ids)
        logging.warning("Falha ao enfileirar cupom em lote: %s", str(exc))

    context.user_data.pop("coupon_message", None)

    await query.edit_message_reply_markup(reply_markup=None)
    send_admin_alert(
        "✅ Cupom enfileirado para envio\n"
        f"Mensagens enfileiradas: {queued_count}\n"
        f"Falhas ao enfileirar: {fail_count}\n\n"
        "ℹ️ O envio será feito pelo notification worker."
    )

    return ConversationHandler.END


async def cupom_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("coupon_message", None)
    await update.message.reply_text("❌ Comando cupom cancelado.")
    return ConversationHandler.END


# =========================
# LEMBRETE (ADMIN)
# =========================

REMINDER_SELECT, REMINDER_MESSAGE, REMINDER_CONFIRM = range(4, 7)


def _build_product_display_name(name, color):
    if "Básico" in name and color:
        return f"{name} — {color}"
    return name


async def lembrete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    ensure_user(update)

    reply_markup = build_products_keyboard(prefix="reminder_")

    await update.message.reply_text(
        "🔔 Selecione o Kindle para o lembrete:",
        reply_markup=reply_markup
    )

    return REMINDER_SELECT


async def lembrete_select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin_user(update):
        await query.answer("Sem permissão", show_alert=True)
        return ConversationHandler.END

    if query.data is None or not query.data.startswith("reminder_"):
        await query.message.reply_text("❌ Seleção inválida.")
        return ConversationHandler.END

    try:
        product_id = int(query.data.split("_")[1])
    except (TypeError, ValueError):
        await query.message.reply_text("❌ Produto inválido.")
        return ConversationHandler.END

    product = next((p for p in get_active_products() if p[0] == product_id), None)
    if not product:
        await query.message.reply_text("❌ Produto não encontrado.")
        return ConversationHandler.END

    _pid, asin, name, color = product
    context.user_data["reminder_product_id"] = product_id
    context.user_data["reminder_product_name"] = _build_product_display_name(name, color)
    context.user_data["reminder_button_url"] = build_affiliate_link(asin)

    await query.message.reply_text(
        "✍️ Agora envie a mensagem personalizada do lembrete."
    )

    return REMINDER_MESSAGE


async def lembrete_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    reminder_message = (update.message.text or "").strip()
    if not reminder_message:
        await update.message.reply_text("❌ Mensagem vazia. Envie o texto do lembrete.")
        return REMINDER_MESSAGE

    if len(reminder_message) > 4000:
        await update.message.reply_text("❌ Mensagem muito longa. Máximo de 4000 caracteres.")
        return REMINDER_MESSAGE

    product_name = context.user_data.get("reminder_product_name")
    if not product_name:
        await update.message.reply_text("❌ Produto do lembrete não encontrado. Reinicie com /lembrete.")
        return ConversationHandler.END

    context.user_data["reminder_message"] = reminder_message

    reply_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Sim", callback_data="reminder_yes"),
            InlineKeyboardButton("❌ Não", callback_data="reminder_no")
        ]
    ])

    await update.message.reply_text(
        "Confirmar envio do lembrete?\n\n"
        f"Produto: {product_name}\n\n"
        f"{reminder_message}",
        reply_markup=reply_markup
    )

    return REMINDER_CONFIRM


async def lembrete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin_user(update):
        await query.answer("Sem permissão", show_alert=True)
        return ConversationHandler.END

    if query.data == "reminder_no":
        context.user_data.pop("reminder_product_id", None)
        context.user_data.pop("reminder_product_name", None)
        context.user_data.pop("reminder_button_url", None)
        context.user_data.pop("reminder_message", None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Envio de lembrete cancelado.")
        return ConversationHandler.END

    product_id = context.user_data.get("reminder_product_id")
    product_name = context.user_data.get("reminder_product_name")
    button_url = context.user_data.get("reminder_button_url")
    reminder_message = context.user_data.get("reminder_message")

    if not product_id or not reminder_message:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Dados do lembrete incompletos. Reinicie com /lembrete.")
        return ConversationHandler.END

    recipients = get_users_who_received_product_alert(product_id)
    user_ids = [row[0] for row in recipients]

    if not user_ids:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "ℹ️ Ninguém recebeu alerta inicial desse produto ainda."
        )
        return ConversationHandler.END

    try:
        queued_count = enqueue_notifications_bulk(
            user_ids=user_ids,
            message=reminder_message,
            product_id=product_id,
            button_url=button_url,
            old_price=None,
            new_price=None,
            is_prime=False,
        )
        fail_count = max(0, len(user_ids) - queued_count)
    except Exception as exc:
        queued_count = 0
        fail_count = len(user_ids)
        logging.warning("Falha ao enfileirar lembrete em lote: %s", str(exc))

    context.user_data.pop("reminder_product_id", None)
    context.user_data.pop("reminder_product_name", None)
    context.user_data.pop("reminder_button_url", None)
    context.user_data.pop("reminder_message", None)

    await query.edit_message_reply_markup(reply_markup=None)
    send_admin_alert(
        "✅ Lembrete enfileirado para envio\n"
        f"Produto: {product_name}\n"
        f"Mensagens enfileiradas: {queued_count}\n"
        f"Falhas ao enfileirar: {fail_count}\n\n"
        "ℹ️ Destinatários: apenas quem recebeu o alerta inicial."
    )

    return ConversationHandler.END


async def lembrete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("reminder_product_id", None)
    context.user_data.pop("reminder_product_name", None)
    context.user_data.pop("reminder_button_url", None)
    context.user_data.pop("reminder_message", None)
    await update.message.reply_text("❌ Comando lembrete cancelado.")
    return ConversationHandler.END


# =========================
# RESUMO (ADMIN)
# =========================

SUMMARY_EVENT_NAME, SUMMARY_PERIOD, SUMMARY_CONFIRM = range(7, 10)
PROMO_END_SELECT, PROMO_END_DATE, PROMO_END_CONFIRM = range(10, 13)


def _parse_summary_period(text):
    raw = (text or "").strip()
    if not raw:
        return None, None, None

    normalized = raw.lower().replace(" até ", " a ")

    if " a " in normalized:
        pieces = [piece.strip() for piece in normalized.split(" a ", 1)]
        if len(pieces) != 2:
            return None, None, None
        left, right = pieces[0], pieces[1]
        try:
            start_date = datetime.strptime(left, "%d/%m/%Y").date()
            end_date = datetime.strptime(right, "%d/%m/%Y").date()
        except ValueError:
            return None, None, None
    else:
        try:
            start_date = datetime.strptime(normalized, "%d/%m/%Y").date()
            end_date = start_date
        except ValueError:
            return None, None, None

    if end_date < start_date:
        return None, None, None

    label = f"{start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}"
    return start_date, end_date, label


def _build_campaign_summary_message(event_name, rows):
    lines = [
        f"🎉 Resumo do evento: {event_name}",
        "",
        "Menores preços encontrados:",
    ]

    for _pid, name, color, _asin, lowest_price in rows:
        display_name = _build_product_display_name(name, color)
        lines.append(f"- {display_name}: R$ {float(lowest_price):.2f}")

    lines.extend([
        "",
        "🔍 O bot continuará monitorando promoções para você.",
    ])

    return "\n".join(lines)


async def resumo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    ensure_user(update)

    await update.message.reply_text(
        "📝 Qual o nome do evento para o resumo da campanha?"
    )

    return SUMMARY_EVENT_NAME


async def resumo_receive_event_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    event_name = (update.message.text or "").strip()
    if not event_name:
        await update.message.reply_text("❌ Nome do evento vazio. Envie novamente.")
        return SUMMARY_EVENT_NAME

    context.user_data["summary_event_name"] = event_name

    await update.message.reply_text(
        "📅 Informe o período do resumo.\n"
        "Formato: DD/MM/AAAA a DD/MM/AAAA\n"
        "Exemplo: 10/07/2026 a 16/07/2026\n\n"
        "Se preferir um único dia: DD/MM/AAAA"
    )

    return SUMMARY_PERIOD


async def resumo_receive_period(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    event_name = context.user_data.get("summary_event_name")
    if not event_name:
        await update.message.reply_text("❌ Evento não encontrado. Reinicie com /resumo.")
        return ConversationHandler.END

    start_date, end_date, _period_label = _parse_summary_period(update.message.text or "")
    if start_date is None:
        await update.message.reply_text(
            "❌ Período inválido. Use:\n"
            "DD/MM/AAAA a DD/MM/AAAA\n"
            "ou somente DD/MM/AAAA"
        )
        return SUMMARY_PERIOD

    rows = get_products_lowest_prices(
        limit_products=200,
        start_date=start_date,
        end_date=end_date,
    )
    if not rows:
        await update.message.reply_text(
            "ℹ️ Sem histórico de preços suficiente para montar o resumo nesse período."
        )
        return SUMMARY_PERIOD

    summary_message = _build_campaign_summary_message(event_name, rows)
    context.user_data["summary_message"] = summary_message

    reply_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar envio", callback_data="summary_yes"),
            InlineKeyboardButton("❌ Cancelar", callback_data="summary_no"),
        ]
    ])

    await update.message.reply_text(
        "Confirma o envio deste resumo?\n\n"
        f"{summary_message}",
        reply_markup=reply_markup
    )

    return SUMMARY_CONFIRM


async def resumo_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin_user(update):
        await query.answer("Sem permissão", show_alert=True)
        return ConversationHandler.END

    if query.data == "summary_no":
        context.user_data.pop("summary_message", None)
        context.user_data.pop("summary_event_name", None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Envio do resumo cancelado.")
        return ConversationHandler.END

    summary_message = context.user_data.get("summary_message")
    if not summary_message:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Resumo não encontrado. Reinicie com /resumo.")
        return ConversationHandler.END

    users = get_users_with_active_monitorings()
    user_ids = [user_id for user_id, _telegram_id in users]

    if not user_ids:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("ℹ️ Não há usuários ativos para receber o resumo.")
        return ConversationHandler.END

    try:
        queued_count = enqueue_notifications_bulk(
            user_ids=user_ids,
            message=summary_message,
            product_id=None,
            button_url=None,
            old_price=None,
            new_price=None,
            is_prime=False,
        )
        fail_count = max(0, len(user_ids) - queued_count)
    except Exception as exc:
        queued_count = 0
        fail_count = len(user_ids)
        logging.warning("Falha ao enfileirar resumo em lote: %s", str(exc))

    context.user_data.pop("summary_message", None)
    context.user_data.pop("summary_event_name", None)

    await query.edit_message_reply_markup(reply_markup=None)
    send_admin_alert(
        "✅ Resumo de campanha enfileirado\n"
        f"Mensagens enfileiradas: {queued_count}\n"
        f"Falhas ao enfileirar: {fail_count}\n\n"
        "ℹ️ O envio será feito pelo notification worker."
    )

    return ConversationHandler.END


async def resumo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("summary_message", None)
    context.user_data.pop("summary_event_name", None)
    await update.message.reply_text("❌ Comando resumo cancelado.")
    return ConversationHandler.END


# =========================
# ENCERRAR OFERTA (ADMIN)
# =========================

def _parse_promo_end_date(text):
    raw = (text or "").strip().lower()
    if not raw:
        return None

    if raw in {"hoje"}:
        return date.today()

    try:
        return datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        return None


def _clear_promo_end_context(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("promo_end_product_id", None)
    context.user_data.pop("promo_end_product_name", None)
    context.user_data.pop("promo_end_cutoff_iso", None)


async def encerrar_oferta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    ensure_user(update)

    reply_markup = build_products_keyboard(prefix="endpromo_")

    await update.message.reply_text(
        "🛑 Selecione o Kindle para encerrar os alertas da promoção atual:",
        reply_markup=reply_markup
    )

    return PROMO_END_SELECT


async def encerrar_oferta_select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin_user(update):
        await query.answer("Sem permissão", show_alert=True)
        return ConversationHandler.END

    if query.data is None or not query.data.startswith("endpromo_"):
        await query.message.reply_text("❌ Seleção inválida.")
        return ConversationHandler.END

    try:
        product_id = int(query.data.split("_")[1])
    except (TypeError, ValueError):
        await query.message.reply_text("❌ Produto inválido.")
        return ConversationHandler.END

    product = next((p for p in get_active_products() if p[0] == product_id), None)
    if not product:
        await query.message.reply_text("❌ Produto não encontrado.")
        return ConversationHandler.END

    _pid, _asin, name, color = product
    product_name = _build_product_display_name(name, color)

    promotion_state = get_active_product_promotion_state(
        product_id,
        require_approved=is_promotion_approval_enabled(),
    )
    if promotion_state is None:
        await query.message.reply_text(
            f"ℹ️ Não há promoção aprovada ativa para {product_name} no momento."
        )
        return ConversationHandler.END

    old_price, new_price, is_prime, started_at = promotion_state
    prime_tag = " (Prime)" if bool(is_prime) else ""
    started_text = started_at.strftime("%d/%m/%Y %H:%M") if started_at else "desconhecido"

    context.user_data["promo_end_product_id"] = product_id
    context.user_data["promo_end_product_name"] = product_name

    await query.message.reply_text(
        "Promoção ativa encontrada:\n"
        f"Produto: {product_name}{prime_tag}\n"
        f"De: R$ {float(old_price):.2f} Por: R$ {float(new_price):.2f}\n"
        f"Início: {started_text}\n\n"
        "📅 Informe a data de corte (dd/mm/aaaa) para parar alertas dessa promoção.\n"
        "Exemplo: 31/07/2026\n"
        "Você também pode enviar 'hoje' para encerrar imediatamente."
    )

    return PROMO_END_DATE


async def encerrar_oferta_receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        await update.message.reply_text(
            "❌ Você não tem permissão para usar este comando."
        )
        return ConversationHandler.END

    product_id = context.user_data.get("promo_end_product_id")
    product_name = context.user_data.get("promo_end_product_name")

    if not product_id or not product_name:
        await update.message.reply_text("❌ Produto não encontrado. Reinicie com /encerraroferta.")
        return ConversationHandler.END

    cutoff_date = _parse_promo_end_date(update.message.text if update.message else "")
    if cutoff_date is None:
        await update.message.reply_text(
            "❌ Data inválida. Use o formato dd/mm/aaaa (ex.: 31/07/2026) ou 'hoje'."
        )
        return PROMO_END_DATE

    context.user_data["promo_end_cutoff_iso"] = cutoff_date.isoformat()

    behavior = (
        "encerrar imediatamente"
        if cutoff_date <= date.today()
        else f"encerrar a partir de {cutoff_date.strftime('%d/%m/%Y')}"
    )

    reply_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar", callback_data="endpromo_confirm_yes"),
            InlineKeyboardButton("❌ Cancelar", callback_data="endpromo_confirm_no")
        ]
    ])

    await update.message.reply_text(
        "Confirmar regra de encerramento?\n\n"
        f"Produto: {product_name}\n"
        f"Ação: {behavior}",
        reply_markup=reply_markup
    )

    return PROMO_END_CONFIRM


async def encerrar_oferta_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin_user(update):
        await query.answer("Sem permissão", show_alert=True)
        return ConversationHandler.END

    if query.data == "endpromo_confirm_no":
        _clear_promo_end_context(context)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Encerramento de oferta cancelado.")
        return ConversationHandler.END

    product_id = context.user_data.get("promo_end_product_id")
    product_name = context.user_data.get("promo_end_product_name")
    cutoff_iso = context.user_data.get("promo_end_cutoff_iso")

    if not product_id or not product_name or not cutoff_iso:
        _clear_promo_end_context(context)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Dados incompletos. Reinicie com /encerraroferta.")
        return ConversationHandler.END

    cutoff_date = datetime.strptime(cutoff_iso, "%Y-%m-%d").date()

    updated = set_product_promotion_stop_date(product_id, cutoff_date)

    if not updated:
        _clear_promo_end_context(context)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "ℹ️ A promoção já não está ativa para esse produto."
        )
        return ConversationHandler.END

    if cutoff_date <= date.today():
        deactivate_product_promotion_state(product_id)
        status_line = "🛑 Encerrada imediatamente."
    else:
        status_line = f"📅 Encerramento agendado para {cutoff_date.strftime('%d/%m/%Y')}."

    _clear_promo_end_context(context)

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ Regra aplicada para {product_name}.\n"
        f"{status_line}\n"
        "A partir da data de corte, novos usuários não receberão mais o alerta dessa promoção."
    )

    return ConversationHandler.END


async def encerrar_oferta_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_promo_end_context(context)
    await update.message.reply_text("❌ Comando encerrar oferta cancelado.")
    return ConversationHandler.END


# =========================
# STATUS
# =========================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ensure_user(update)

    telegram_id = update.effective_user.id

    products = get_user_products(telegram_id)

  

    if not products:

        await update.message.reply_text(
            "💤 Você ainda não está monitorando nenhum Kindle."
        )

        return

    message = "📊 Seus monitoramentos:\n\n"
    links_keyboard = []

    for product in products:

        name, color, created_at, product_id, asin = product
        display_name = name

        # só mostra cor no Kindle básico
        if "Básico" in display_name and color:
            display_name += f" — {color}"

        affiliate_link = build_affiliate_link(asin)
        links_keyboard.append([
            InlineKeyboardButton(
                text=f"🛒 {display_name}",
                url=affiliate_link
            )
        ])

        last_prices = get_last_prices(product_id)

        if last_prices is None:
            price_text = "indisponível"
        else:
            # Fallback: if latest row has None, use the most recent registered
            # value for that specific field in price history.
            history = get_product_price_history(product_id, limit=100)

            public_price = last_prices.public_price
            prime_price = last_prices.prime_price

            if public_price is None:
                for hist_public, _ in history:
                    if hist_public is not None:
                        public_price = hist_public
                        break

            if prime_price is None:
                for _, hist_prime in history:
                    if hist_prime is not None:
                        prime_price = hist_prime
                        break

            # Display rule:
            # - default: show public as generic price (without label)
            # - exception: if prime is lower than public, show prime value
            if public_price is None and prime_price is None:
                price_text = "indisponível"
            elif public_price is None:
                price_text = f"R$ {prime_price:.2f}"
            elif prime_price is not None and prime_price < public_price:
                price_text = f"R$ {prime_price:.2f}"
            else:
                price_text = f"R$ {public_price:.2f}"

        message += (
            f"✨ {display_name}\n"
            f"💵 Último preço:\n{price_text}\n"
            f"⏱️ Desde: {created_at.strftime('%d/%m/%Y')}\n\n"
        )

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(links_keyboard)
    )


# =========================
# REMOVER
# =========================

async def remover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    ensure_user(update)

    telegram_id = update.effective_user.id

    products = get_user_products(telegram_id)


    if not products:

        await update.message.reply_text(
            "💤 Você não possui monitoramentos ativos."
        )

        return

    keyboard = []

    for product in products:

        name, color, created_at, product_id, asin = product

        # só mostra cor no Kindle básico
        if "Básico" in name and color:
            name += f" — {color}"

        button_text = name

       

        keyboard.append([
            InlineKeyboardButton(
                text=f"❌ {button_text}",
                callback_data=f"remove_{product_id}"
            )
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Escolha qual monitoramento deseja remover:",
        reply_markup=reply_markup
    )



# =========================
# MENSAGENS ALEATÓRIAS
# =========================

async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        return

    if update.effective_chat.type != "private":
        return

    if update.message is None:
        return
    
    ensure_user(update)
    
    await update.message.reply_text(
        get_welcome_message(update)
    )


# =========================
# CLICK NOS BOTÕES
# =========================

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ensure_user(update)

    query = update.callback_query

    await query.answer()

    data = query.data

    # =========================
    # APROVACAO DE PROMOCAO (ADMIN)
    # =========================

    if data.startswith("approval_yes_") or data.startswith("approval_no_"):
        if not is_admin_user(update):
            await query.answer("Apenas o administrador pode decidir.", show_alert=True)
            return

        parts = data.split("_")
        if len(parts) != 3:
            await query.answer("Solicitacao invalida.", show_alert=True)
            return

        approved = parts[1] == "yes"

        try:
            approval_id = int(parts[2])
        except ValueError:
            await query.answer("Solicitacao invalida.", show_alert=True)
            return

        approval_request = get_alert_approval_request(approval_id)
        if approval_request is None:
            await query.answer("Solicitacao nao encontrada.", show_alert=True)
            return

        updated = resolve_alert_approval_request(
            approval_id=approval_id,
            approved=approved,
            decided_by=query.from_user.id,
            source="telegram-inline"
        )

        if not updated:
            await query.answer("Essa solicitacao ja foi decidida.", show_alert=True)
            return

        product_name = approval_request[2]
        old_price = approval_request[3]
        new_price = approval_request[4]
        is_prime = bool(approval_request[5])
        prime_tag = " (Prime)" if is_prime else ""

        decision_text = "APROVADO" if approved else "CANCELADO"

        await query.edit_message_reply_markup(reply_markup=None)

        immediate_result = ""
        if approved:
            sent_count, pending_count = dispatch_approved_promotion_alerts(approval_id)
            immediate_result = (
                f"\nAlertas enfileirados agora: {sent_count}."
                f" Pendentes/falhas: {pending_count}."
            )

        await query.message.reply_text(
            f"{decision_text}: {product_name}{prime_tag}\n"
            f"De: R$ {old_price:.2f} Por: R$ {new_price:.2f}"
            f"{immediate_result}"
        )
        await query.answer("Decisao registrada.")
        return

    # =========================
    # MONITORAR
    # =========================

    if data.startswith("track_"):

        product_id = int(data.split("_")[1])

        telegram_id = query.from_user.id

        user = get_user_by_telegram_id(telegram_id)
        if not user:
            await query.message.reply_text(
                "❌ Não foi possível identificar seu usuário. Tente /start novamente."
            )
            return

        internal_user_id = user[0]

        products = get_active_products()

        selected_product = None

        for product in products:
            if product[0] == product_id:
                selected_product = product
                break

        if not selected_product:

            await query.message.reply_text(
                "❌ Produto não encontrado."
            )

            return

        _, _, product_name, color = selected_product

        was_added = add_user_product(telegram_id, product_id)

        if not was_added:

            await query.message.reply_text(
                f"👀 Você já está monitorando o {product_name}."
            )

            return

        last_prices = get_last_prices(product_id)
        current_best_price = None
        current_best_is_prime = False

        if last_prices is not None:
            current_best_price, current_best_is_prime = get_best_price_info(
                last_prices.public_price,
                last_prices.prime_price
            )

        promotion_state = get_active_product_promotion_state(
            product_id,
            require_approved=is_promotion_approval_enabled(),
        )

        initialize_user_product_state(
            telegram_id,
            product_id,
            last_seen_price=current_best_price,
            last_notified_price=None
        )

        if promotion_state is not None and current_best_price is not None:
            promotion_old_price, promotion_new_price, promotion_is_prime, _started_at = promotion_state

            if promotion_old_price is None or promotion_new_price is None:
                promotion_old_price = None
                promotion_new_price = None

        if (
            promotion_state is not None
            and current_best_price is not None
            and promotion_old_price is not None
            and promotion_new_price is not None
        ):
            queue_price_alert(
                product_id,
                float(promotion_old_price),
                float(promotion_new_price),
                prime=bool(promotion_is_prime),
                target_users=[(internal_user_id, telegram_id)]
            )

        if "Básico" in product_name and color:
            product_name += f" — {color}"

        await query.message.reply_text(
            f"✅ Beleza! Vou te avisar quando o preço do "
            f"{product_name} baixar. Enquanto isso, você pode acompanhar o preço atual usando o comando /status.\n\n"
            "🔔 Não se esqueça de ativar as notificações do Telegram para não perder nenhuma oferta!"
        )

        logging.info(
        f"Usuário {telegram_id} começou monitorar "
        f"{product_name}"
        )

    # =========================
    # REMOVER
    # =========================

    elif data.startswith("remove_"):

        product_id = int(data.split("_")[1])

        from database import (
            remove_user_product,
            get_product_by_id
        )

        product = get_product_by_id(product_id)

        if not product:

            await query.message.reply_text(
                "❌ Produto não encontrado."
            )

            return

        _, _, name, color = product

        remove_user_product(
            query.from_user.id,
            product_id
        )

        if "Básico" in name and color:
            name += f" — {color}"

        await query.message.reply_text(
            f"🗑️ Monitoramento removido:\n{name}"
        )

        logging.info(
        f"Usuário {query.from_user.id} removeu "
        f"{name}"
        )


# =========================
# MAIN
# =========================

def main():

    app = Application.builder().token(TOKEN).build()
    
    app.add_error_handler(error_handler)


    # comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ajuda", ajuda))
    app.add_handler(CommandHandler("kindle", kindle))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("remover", remover))
    app.add_handler(CommandHandler("relatorio", relatorio))

    manual_handler = ConversationHandler(
        entry_points=[CommandHandler("manual", manual)],
        states={
            MANUAL_SELECT: [
                CallbackQueryHandler(
                    manual_select_product,
                    pattern=r"^manual_"
                )
            ],
            MANUAL_PRICES: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    manual_prices
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", manual_cancel)
        ],
        per_chat=True,
        per_user=True
    )

    app.add_handler(manual_handler)

    cupom_handler = ConversationHandler(
        entry_points=[CommandHandler("cupom", cupom)],
        states={
            COUPON_MESSAGE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cupom_receive_message
                )
            ],
            COUPON_CONFIRM: [
                CallbackQueryHandler(
                    cupom_confirm,
                    pattern=r"^coupon_(yes|no)$"
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cupom_cancel)
        ],
        per_chat=True,
        per_user=True
    )

    app.add_handler(cupom_handler)

    lembrete_handler = ConversationHandler(
        entry_points=[CommandHandler("lembrete", lembrete)],
        states={
            REMINDER_SELECT: [
                CallbackQueryHandler(
                    lembrete_select_product,
                    pattern=r"^reminder_"
                )
            ],
            REMINDER_MESSAGE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    lembrete_receive_message
                )
            ],
            REMINDER_CONFIRM: [
                CallbackQueryHandler(
                    lembrete_confirm,
                    pattern=r"^reminder_(yes|no)$"
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", lembrete_cancel)
        ],
        per_chat=True,
        per_user=True
    )

    app.add_handler(lembrete_handler)

    resumo_handler = ConversationHandler(
        entry_points=[CommandHandler("resumo", resumo)],
        states={
            SUMMARY_EVENT_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    resumo_receive_event_name
                )
            ],
            SUMMARY_PERIOD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    resumo_receive_period
                )
            ],
            SUMMARY_CONFIRM: [
                CallbackQueryHandler(
                    resumo_confirm,
                    pattern=r"^summary_(yes|no)$"
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", resumo_cancel)
        ],
        per_chat=True,
        per_user=True
    )

    app.add_handler(resumo_handler)

    encerrar_oferta_handler = ConversationHandler(
        entry_points=[CommandHandler("encerraroferta", encerrar_oferta)],
        states={
            PROMO_END_SELECT: [
                CallbackQueryHandler(
                    encerrar_oferta_select_product,
                    pattern=r"^endpromo_"
                )
            ],
            PROMO_END_DATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    encerrar_oferta_receive_date
                )
            ],
            PROMO_END_CONFIRM: [
                CallbackQueryHandler(
                    encerrar_oferta_confirm,
                    pattern=r"^endpromo_confirm_(yes|no)$"
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", encerrar_oferta_cancel)
        ],
        per_chat=True,
        per_user=True
    )

    app.add_handler(encerrar_oferta_handler)

    # botões
    app.add_handler(CallbackQueryHandler(button_click))

    # mensagens aleatórias
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            unknown_message
        )
    )

    print("🤖 Bot rodando...")

    app.run_polling()


if __name__ == "__main__":
    main()