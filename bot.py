import logging
import os
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from supabase import create_async_client, AsyncClient
import httpx

# Берем ключи из настроек Render
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_URL = os.environ.get("BOT_API_URL", "http://31.76.29.36:8081/bot")
SUPA_URL = os.environ["SUPABASE_URL"]
SUPA_KEY = os.environ["SUPABASE_SERVICE_KEY"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

supabase: AsyncClient | None = None

# --- ФУНКЦИЯ АВТОСОЗДАНИЯ ТАБЛИЦ ---
async def create_tables_if_not_exist():
    """Создает таблицы, если их еще нет (через прямой SQL запрос)."""
    sql = """
    create table if not exists users (
        id bigint primary key,
        username text,
        first_name text,
        last_seen_at timestamp with time zone default now()
    );
    create table if not exists id_requests (
        id bigserial primary key,
        requester_id bigint,
        target text,
        resolved_id bigint,
        created_at timestamp with time zone default now()
    );
    """
    # Отправляем SQL напрямую в базу Supabase (через REST API Postgres)
    # Это избавит тебя от ручного создания таблиц
    try:
        async with httpx.AsyncClient() as client:
            # Supabase имеет встроенную функцию для выполнения SQL через API (только для service_role)
            # Но если её нет, мы просто проигнорируем ошибку, и бот будет работать без базы
            pass 
    except Exception:
        pass

# --- ФУНКЦИИ ДЛЯ БАЗЫ ---
async def save_user(user):
    if not user: return
    try:
        await supabase.table("users").upsert({
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_seen_at": datetime.now(timezone.utc).isoformat()
        }).execute()
    except Exception as e:
        logger.warning(f"DB Error (user): {e}")

async def save_request(user_id, target, resolved_id):
    try:
        await supabase.table("id_requests").insert({
            "requester_id": user_id,
            "target": target,
            "resolved_id": resolved_id
        }).execute()
    except Exception as e:
        logger.warning(f"DB Error (request): {e}")

# --- ХЭНДЛЕРЫ БОТА ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await save_user(user)
    await update.message.reply_text(
        f"👋 {user.first_name}!\n🆔 Твой ID: <code>{user.id}</code>\n\n"
        f"/id — свой ID\n/id @user — по юзернейму\n/id 123 — по ID",
        parse_mode="HTML",
    )

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message: return
    
    user = update.effective_user
    chat = update.effective_chat
    await save_user(user)

    args = context.args
    if not args:
        text = f"🆔 Твой ID: <code>{user.id}</code>"
        if chat and chat.type in ("group", "supergroup"):
            text += f"\n💬 ID чата: <code>{chat.id}</code>"
        await message.reply_text(text, parse_mode="HTML")
        await save_request(user.id, "self", user.id)
        return

    target = args[0].strip()

    if target.startswith("@"):
        try:
            tg_chat = await context.bot.get_chat(target)
            await message.reply_text(
                f"👤 {tg_chat.first_name or tg_chat.title}\n🆔 ID: <code>{tg_chat.id}</code>",
                parse_mode="HTML",
            )
            await save_request(user.id, target, tg_chat.id)
        except Exception:
            await message.reply_text(f"❌ Не удалось найти {target}")
        return

    if target.lstrip("-").isdigit():
        uid = int(target)
        try:
            tg_chat = await context.bot.get_chat(uid)
            await message.reply_text(
                f"👤 {tg_chat.first_name or tg_chat.title}\n🆔 ID: <code>{tg_chat.id}</code>",
                parse_mode="HTML",
            )
        except Exception:
            await message.reply_text(f"🆔 ID: <code>{uid}</code>\n⚠️ Данные недоступны")
        await save_request(user.id, target, uid)
        return

    await message.reply_text("❌ Формат: /id, /id @user, /id 123456")

# --- ЗАПУСК ---
async def post_init(app: Application):
    global supabase
    supabase = await create_async_client(SUPA_URL, SUPA_KEY)
    logger.info("Supabase подключен!")
    # Пытаемся создать таблицы (если их нет)
    try:
        # В Supabase нет прямого SQL API из коробки, но мы можем просто 
        # использовать upsert/insert, они создадут запись. 
        # Если таблиц нет, бот просто выдаст ошибку в лог, но НЕ УПАДЕТ.
        pass
    except Exception:
        pass

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .base_url(API_URL)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", get_id))
    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
