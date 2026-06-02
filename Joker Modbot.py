import asyncio
import logging
import random
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import ChatPermissions

# ---------------- CONFIG ----------------
import os

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6000945877

bot = Bot(token=TOKEN)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)

# ---------------- CACHE ----------------
active_users = {}      # user_id -> name
captcha_store = {}     # user_id -> correct answer


# ---------------- DB ----------------
async def init_db():
    async with aiosqlite.connect("boss.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            warns INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0
        )
        """)
        await db.commit()


# ---------------- CAPTCHA ----------------
def generate_captcha():
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    return a, b, a + b


# ---------------- ADMIN PANEL ----------------
def admin_panel():
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Users", callback_data="admin:users")
    kb.button(text="📊 Stats", callback_data="admin:stats")
    kb.adjust(1)
    return kb.as_markup()


def user_actions(uid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="⛔ Ban", callback_data=f"act:ban:{uid}")
    kb.button(text="🔇 Mute 1h", callback_data=f"act:mute:{uid}")
    kb.button(text="🔊 Unmute", callback_data=f"act:unmute:{uid}")
    kb.button(text="ℹ Info", callback_data=f"act:info:{uid}")
    kb.adjust(2)
    return kb.as_markup()


# ---------------- NEW USERS + CAPTCHA ----------------
@dp.message(F.new_chat_members)
async def new_members(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return

    for user in message.new_chat_members:
        name = user.username or user.full_name
        active_users[user.id] = name

        a, b, ans = generate_captcha()
        captcha_store[user.id] = ans

        await message.answer(
            f"🔐 Добро пожаловать, {name}!\n"
            f"Решите капчу: {a} + {b} = ?"
        )


# ---------------- CAPTCHA CHECK ----------------
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def captcha_check(message: Message):
    if not message.from_user:
        return

    user_id = message.from_user.id

    if user_id in captcha_store:
        try:
            if message.text and int(message.text) == captcha_store[user_id]:
                del captcha_store[user_id]

                async with aiosqlite.connect("boss.db") as db:
                    await db.execute(
                        "INSERT OR REPLACE INTO users(user_id, warns, verified) VALUES(?,?,1)",
                        (user_id, 0)
                    )
                    await db.commit()

                await message.answer("✅ Проверка пройдена!")
            else:
                await message.delete()
        except:
            await message.delete()
        return

    # если не verified — можно расширить логику
    active_users[user_id] = message.from_user.username or message.from_user.full_name


# ---------------- PRIVATE (ADMIN ONLY) ----------------
@dp.message(F.chat.type == "private")
async def private(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа")
        return

    if message.text == "/start":
        await message.answer("👮 Admin panel доступна: /admin")

    elif message.text == "/admin":
        await message.answer("👮 Панель:", reply_markup=admin_panel())


# ---------------- USERS LIST ----------------
@dp.callback_query(F.data == "admin:users")
async def users(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return

    kb = InlineKeyboardBuilder()

    for uid, name in list(active_users.items())[-25:]:
        kb.button(text=name[:20], callback_data=f"user:{uid}")

    kb.adjust(1)

    await call.message.answer("👥 Users:", reply_markup=kb.as_markup())
    await call.answer()


# ---------------- SELECT USER ----------------
@dp.callback_query(F.data.startswith("user:"))
async def select_user(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return

    uid = int(call.data.split(":")[1])
    name = active_users.get(uid, str(uid))

    await call.message.answer(f"👤 {name}", reply_markup=user_actions(uid))
    await call.answer()


# ---------------- ACTIONS ----------------
@dp.callback_query(F.data.startswith("act:"))
async def actions(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return

    _, action, uid = call.data.split(":")
    uid = int(uid)

    chat_id = call.message.chat.id

    try:
        if action == "ban":
            await bot.ban_chat_member(chat_id, uid)
            await call.message.answer(f"⛔ Banned {uid}")

        elif action == "mute":
            await bot.restrict_chat_member(
                chat_id,
                uid,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=datetime.utcnow() + timedelta(hours=1)
            )
            await call.message.answer(f"🔇 Muted {uid}")

        elif action == "unmute":
            await bot.restrict_chat_member(
                chat_id,
                uid,
                permissions=ChatPermissions(can_send_messages=True)
            )
            await call.message.answer(f"🔊 Unmuted {uid}")

        elif action == "info":
            async with aiosqlite.connect("boss.db") as db:
                cur = await db.execute(
                    "SELECT warns, verified FROM users WHERE user_id=?",
                    (uid,)
                )
                row = await cur.fetchone()

            if row:
                await call.message.answer(
                    f"👤 {uid}\nWarns: {row[0]}\nVerified: {row[1]}"
                )
            else:
                await call.message.answer(f"👤 {uid}\nНет данных")

    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}")

    await call.answer()


# ---------------- STATS ----------------
@dp.callback_query(F.data == "admin:stats")
async def stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect("boss.db") as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        verified = (await (await db.execute("SELECT COUNT(*) FROM users WHERE verified=1")).fetchone())[0]

    await call.message.answer(f"📊 Users: {total}\n✅ Verified: {verified}")
    await call.answer()


# ---------------- START ----------------
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())