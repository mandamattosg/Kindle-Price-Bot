from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

import logging
import traceback
from notifications import send_admin_alert
from dotenv import load_dotenv
import os

from database import (
    get_active_products,
    save_user,
    add_user_product,
    get_user_products,
    get_latest_price
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

def ensure_user(update: Update):
    user = update.effective_user

    save_user(
        telegram_id=user.id,
        username=user.username
    )

async def error_handler(update, context):

    logging.error(
        "❌ Erro no kindle bot",
        exc_info=context.error
    )

    try:

        error_text = "".join(
            traceback.format_exception(
                type(context.error),
                context.error,
                context.error.__traceback__
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


# =========================
# KEYBOARD DE PRODUTOS
# =========================

def build_products_keyboard():

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
                callback_data=f"track_{product_id}"
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

    await update.message.reply_text(WELCOME_MESSAGE)


# =========================
# AJUDA
# =========================

async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ensure_user(update)

    await update.message.reply_text(WELCOME_MESSAGE)


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

    for product in products:

        name, color, created_at, product_id = product

        # só mostra cor no Kindle básico
        if "Básico" in name and color:
            name += f" — {color}"

        price = get_latest_price(product_id)


        price_text = (
            f"R$ {price:.2f}"
            if price is not None
            else "indisponível"
        )

        message += (
            f"✨ {name}\n"
            f"💵 Último preço: {price_text}\n"
            f"⏱️ Desde: {created_at.strftime('%d/%m/%Y')}\n\n"
        )

    await update.message.reply_text(message)


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

        name, color, created_at, product_id = product

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

    ensure_user(update)

    await update.message.reply_text(WELCOME_MESSAGE)


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

        user_id = query.from_user.id

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

        was_added = add_user_product(user_id, product_id)

        if not was_added:

            await query.message.reply_text(
                f"👀 Você já está monitorando o {product_name}."
            )

            return
        
        if "Básico" in product_name and color:
            product_name += f" — {color}"

        await query.message.reply_text(
            f"✅ Beleza! Vou te avisar quando o preço do "
            f"{product_name} baixar. Enquanto isso, você pode acompanhar o preço atual usando o comando /status.\n\n"
            "Não se esqueça de ativar as notificações do Telegram para não perder nenhuma oferta!"
        )

        logging.info(
        f"Usuário {user_id} começou monitorar "
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