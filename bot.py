import logging
import sqlite3
from datetime import datetime, timedelta
import asyncio

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor

# 🔑 Bot tokenini bu yerga qo'y
API_TOKEN = '8106728301:AAEq9OvTowwzbigPMCcAGfJVLqtO1UGmaJY'

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# ---------------------------
# DATABASE
# ---------------------------
conn = sqlite3.connect('data.db')
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    content TEXT,
    review_1 TEXT,  -- ertaga
    review_2 TEXT,  -- 7 kundan keyin
    review_3 TEXT,  -- 30 kundan keyin
    extra_review TEXT  -- qiyin bo‘ldi uchun keyingi kun
)
""")
conn.commit()

# ---------------------------
# START COMMAND
# ---------------------------
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    name = message.from_user.first_name
    await message.reply(
        f"Salom, {name}! 📚\n\nYangi bilim qo‘shish uchun /add yozing.\nMen sizga o‘zim eslataman!"
    )

# ---------------------------
# ADD COMMAND
# ---------------------------
@dp.message_handler(commands=['add'])
async def add(message: types.Message):
    await message.reply("✍️ Yangi o‘rgangan narsani yozing:")

# ---------------------------
# SAVE CONTENT
# ---------------------------
@dp.message_handler()
async def save(message: types.Message):
    user_id = message.from_user.id
    content = message.text

    today = datetime.now()
    review_1 = today + timedelta(days=1)
    review_2 = today + timedelta(days=7)
    review_3 = today + timedelta(days=30)

    cursor.execute(
        "INSERT INTO cards (user_id, content, review_1, review_2, review_3, extra_review) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, content,
         review_1.strftime("%Y-%m-%d"),
         review_2.strftime("%Y-%m-%d"),
         review_3.strftime("%Y-%m-%d"),
         None)
    )
    conn.commit()

    await message.reply("✅ Saqlandi! ⏰ Ertaga, 7 kundan keyin va 30 kundan keyin eslataman.")

# ---------------------------
# REVIEW BUTTONS
# ---------------------------
def get_buttons(card_id):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Esladim", callback_data=f"good_{card_id}"),
        InlineKeyboardButton("❌ Qiyin bo‘ldi", callback_data=f"bad_{card_id}")
    )
    return kb

# ---------------------------
# SEND REVIEWS
# ---------------------------
async def send_reviews(user_id):
    today_str = datetime.now().strftime("%Y-%m-%d")

    cursor.execute(
        "SELECT * FROM cards WHERE user_id=? AND (review_1=? OR review_2=? OR review_3=? OR extra_review=?)",
        (user_id, today_str, today_str, today_str, today_str)
    )
    rows = cursor.fetchall()

    if not rows:
        await bot.send_message(user_id, "📭 Bugun takrorlash yo‘q.")
        return

    for row in rows:
        card_id = row[0]
        content = row[2]

        await bot.send_message(
            user_id,
            f"📚 Bugun takrorlash vaqti!\n\n{content}",
            reply_markup=get_buttons(card_id)
        )

    # Bugun yuborilgan extra_review bo‘shatilsin
    cursor.execute(
        "UPDATE cards SET extra_review=NULL WHERE extra_review=?",
        (today_str,)
    )
    conn.commit()

# ---------------------------
# BUTTON HANDLER
# ---------------------------
@dp.callback_query_handler(lambda c: True)
async def process_callback(callback_query: types.CallbackQuery):
    data = callback_query.data
    action, card_id = data.split("_")
    card_id = int(card_id)

    if action == "bad":
        # Qiyin bo‘ldi → keyingi kunga qo‘shish
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        cursor.execute(
            "UPDATE cards SET extra_review=? WHERE id=?",
            (tomorrow, card_id)
        )
        conn.commit()

    await callback_query.answer("✅ Yangilandi")
# ---------------------------

# MANUAL CHECK (optional)
# ---------------------------
@dp.message_handler(commands=['check'])
async def check_now(message: types.Message):
    await send_reviews(message.from_user.id)

# ---------------------------
# AUTOMATIC DAILY CHECKER
# ---------------------------
async def scheduler():
    while True:
        cursor.execute("SELECT DISTINCT user_id FROM cards")
        users = cursor.fetchall()
        for user in users:
            await send_reviews(user[0])
        await asyncio.sleep(86400)  # 24 soat = 86400 soniya

async def on_startup(dp):
    asyncio.create_task(scheduler())

# ---------------------------
# RUN BOT
# ---------------------------
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
