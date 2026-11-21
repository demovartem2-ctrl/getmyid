import logging
import os
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    MessageHandler, filters,
)
from supabase import create_async_client, AsyncClient

# ===== ENV =====
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_URL   = os.environ.get("BOT_API_URL", "http://31.76.29.36:8081/bot")
SUPA_URL  = os.environ["SUPABASE_URL"]
SUPA_KEY  = os.environ["SUPABASE_SERVICE_KEY"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

supabase: AsyncClient | None = None
now = lambda: datetime.now(timezone.utc).isoformat()


# ---------- DB helpers ----------
async def upsert_user(user, is_start=False, is_id=False):
    if not user:
        return
    try:
        # сначала пробуем обновить
        res = await supabase.table("users").select("id, starts_count, ids_count").eq("id", user.id).execute()
        if res.data:
            row = res.data[0]
            await supabase.table("users").update({
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language_code": user.language_code,
                "last_seen_at": now(),
                "starts_count": row["starts_count"] + (1 if is_start else 0),
                "ids_count": row["ids_count"] + (1 if is_id else 0),
            }).eq("id", user.id).execute()
        else:
            await supabase.table("users").insert({
                "id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language_code": user.language_code,
                "is_bot": user.is_bot,
                "starts_count": 1 if is_start else 0,
                "ids_count": 1 if is_id else 0,
            }).execute()
    except Exception as e:
        logger.warning(f"upsert_user: {e}")


async def upsert_chat(chat):
    if not chat:
        return
    try:
        res = await supabase.table("chats").select("id, messages_count").eq("id", chat.id).execute()
        if res.data:
            await supabase.table("chats").update({
                "title": chat.title,
                "username": chat.username,
                "last_seen_at": now(),
                "messages_count": res.data[0]["messages_count"] + 1,
            }).eq("id", chat.id).execute()
        else:
            await supabase.table("chats").insert({
                "id": chat.id,
                "type": chat.type,
                "title": chat.title,
                "username": chat.username,
                "messages_count": 1,
            }).execute()
    except Exception as e:
        logger.warning(f"upsert_chat: {e}")


async def log_command(user, chat, command, args):
    try:
        await supabase.table("commands_log").insert({
            "user_id": user.id if user else None,
            "chat_id": chat.id if chat else None,
            "chat_type": chat.type if chat else None,
            "command": command,
            "args": " ".join(args) if args else None,
        }).execute()
    except Exception as e:
        logger.warning(f"log_command: {e}")


async def cache_username(uname, uid):
    try:
        await supabase.table("username_cache").upsert(
            {"username": uname.lower(), "user_id": uid, "updated_at": now()},
            on_conflict="username",
        ).execute()
    except Exception as e:
        logger.warning(f"cache_username: {e}")


async def get_cached_username(uname):
    try:
        res = await supabase.table("username_cache").select("user_id").eq("username", uname.lower()).limit(1).execute()
        if res.data:
            return res.data[0]["user_id"]
    except Exception as e:
        logger.warning(f"get_cached_username: {e}")
    return None


# ---------- Global middleware ----------
async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Логируем всё, что приходит."""
    if update.effective_user:
        await upsert_user(update.effective_user)
    if update.effective_chat:
        await upsert_chat(update.effective_chat)


# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user, is_start=True)
    await log_command(user, update.effective_chat, "start", [])
    await update.message.reply_text(
        f"👋 {user.first_name}!\n🆔 Твой ID: <code>{user.id}</code>\n\n"
        f"/id — свой ID\n/id @user — по юзернейму\n/id 123 — по ID\n"
        f"/stats — статистика бота",
        parse_mode="HTML",
    )


async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    user = update.effective_user
    chat = update.effective_chat
    args = context.args

    await upsert_user(user, is_id=True)
    await log_command(user, chat, "id", args)

    # свой ID
    if not args:
        text = f"🆔 Твой ID: <code>{user.id}</code>"
        if chat and chat.type in ("group", "supergroup"):
            text += f"\n💬 ID чата: <code>{chat.id}</code>"
        await message.reply_text(text, parse_mode="HTML")
        return

    target = args[0].strip()

    # @username
    if target.startswith("@"):
        uname = target[1:]
        cached = await get_cached_username(uname)
        if cached:
            await message.reply_text(
                f"👤 @{uname}\n🆔 ID: <code>{cached}</code> (кэш)",
                parse_mode="HTML",
            )
            return
        try:
            tg = await context.bot.get_chat(target)
            await cache_username(uname, tg.id)
            await message.reply_text(
                f"👤 {tg.first_name or tg.title}\n🆔 ID: <code>{tg.id}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"get_chat @{uname}: {e}")
            await message.reply_text(f"❌ Не удалось найти @{uname}")
        return

    # числовой ID
    if target.lstrip("-").isdigit():
        uid = int(target)
        try:
            tg = await context.bot.get_chat(uid)
            name = tg.first_name or tg.title or "—"
            uname = f" (@{tg.username})" if tg.username else ""
            await message.reply_text(
                f"👤 {name}{uname}\n🆔 ID: <code>{tg.id}</code>",
                parse_mode="HTML",
            )
            if tg.username:
                await cache_username(tg.username, tg.id)
        except Exception:
            await message.reply_text(f"🆔 ID: <code>{uid}</code>\n⚠️ Данные недоступны")
        return

    await message.reply_text("❌ Формат: /id, /id @user, /id 123456")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        users = (await supabase.table("users").select("id", count="exact").execute()).count or 0
        chats = (await supabase.table("chats").select("id", count="exact").execute()).count or 0
        cmds  = (await supabase.table("commands_log").select("id", count="exact").execute()).count or 0
        await update.message.reply_text(
            f"📊 <b>Статистика</b>\n"
            f"👥 Юзеров: <code>{users}</code>\n"
            f"💬 Чатов: <code>{chats}</code>\n"
            f"⚙️ Команд: <code>{cmds}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"stats: {e}")
        await update.message.reply_text("❌ Ошибка статистики")


# ---------- Main ----------
async def post_init(app: Application):
    global supabase
    supabase = await create_async_client(SUPA_URL, SUPA_KEY)
    logger.info("Supabase OK")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .base_url(API_URL)
        .post_init(post_init)
        .build()
    )
    # middleware первым
    app.add_handler(MessageHandler(filters.ALL, track), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("stats", stats))
    logger.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
