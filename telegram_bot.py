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
    upsert_user_product_state,
    get_product_promotion_baseline,
    get_best_price_info
)

from alerts import send_price_alert
from alerts import build_affiliate_link
from kindle_scraper import process_product, PriceResult

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
            "/ajuda → mostrar esta mensagem novamente\n/manual → registrar preços manualmente (admin)"
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

        promotion_baseline = None
        if current_best_price is not None:
            promotion_baseline = get_product_promotion_baseline(product_id)

        initialize_user_product_state(
            telegram_id,
            product_id,
            last_seen_price=current_best_price,
            last_notified_price=None
        )

        if promotion_baseline is not None and current_best_price is not None:
            alert_sent = send_price_alert(
                product_id,
                promotion_baseline,
                current_best_price,
                prime=current_best_is_prime,
                target_users=[(internal_user_id, telegram_id)]
            )

            if alert_sent:
                upsert_user_product_state(
                    internal_user_id,
                    product_id,
                    last_seen_price=current_best_price,
                    last_notified_price=current_best_price
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