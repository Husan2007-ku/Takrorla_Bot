import logging
import sqlite3
import os
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor

# ---------------------------
# KONFIGURATSIYA (Tokeningiz shu yerda)
# ---------------------------
API_TOKEN = '8106728301:AAEq9OvTowwzbigPMCcAGfJVLqtO1UGmaJY'

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# ---------------------------
# DATABASE (Ma'lumotlar bazasi)
# ---------------------------
db_path = os.path.join(os.getcwd(), 'data.db')
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    content TEXT,
    review_1 TEXT,  -- 1 kundan keyin
    review_2 TEXT,  -- 7 kundan keyin
    review_3 TEXT,  -- 30 kundan keyin
    extra_review TEXT  -- 'Qiyin bo'ldi' uchun keyingi kun
)
""")
conn.commit()


# ---------------------------
# TUGMALAR (KEYBOARDS)
# ---------------------------

# Asosiy doimiy menyu tugmalari
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Yangi qo'shish"))
    kb.add(KeyboardButton("🔍 Bugun nima bor?"))
    return kb


# Takrorlash paytidagi inline tugmalar
def get_review_keyboard(card_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Esladim", callback_data=f"good_{card_id}"),
        InlineKeyboardButton("❌ Qiyin bo‘ldi", callback_data=f"bad_{card_id}")
    )
    return kb


# ---------------------------
# KOMANDALAR VA TUGMA BOSILISHI
# ---------------------------

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    name = message.from_user.first_name
    await message.reply(
        f"Salom, {name}! 📚\n\nMenyu orqali botni oson ishlatishingiz mumkin:",
        reply_markup=main_menu()
    )


# "Yangi qo'shish" tugmasi bosilganda
@dp.message_handler(lambda message: message.text == "➕ Yangi qo'shish")
async def add_btn(message: types.Message):
    await message.reply("✍️ Yangi o‘rgangan ma'lumotni yozing (matn yuboring):")


# "Bugun nima bor?" tugmasi bosilganda
@dp.message_handler(lambda message: message.text == "🔍 Bugun nima bor?")
async def check_btn(message: types.Message):
    await send_reviews(message.from_user.id)


# ---------------------------
# MA'LUMOTNI SAQLASH (TEXT HANDLER)
# ---------------------------
# Bu handler buyruqlar va tugmalardan boshqa barcha matnlarni bazaga saqlaydi
@dp.message_handler(lambda message: not message.text.startswith(('/', '➕', '🔍')))
async def save_content(message: types.Message):
    user_id = message.from_user.id
    content = message.text

    today = datetime.now()
    r1 = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    r2 = (today + timedelta(days=7)).strftime("%Y-%m-%d")
    r3 = (today + timedelta(days=30)).strftime("%Y-%m-%d")

    cursor.execute(
        "INSERT INTO cards (user_id, content, review_1, review_2, review_3) VALUES (?, ?, ?, ?, ?)",
        (user_id, content, r1, r2, r3)
    )
    conn.commit()

    await message.reply(
        f"✅ Saqlandi!\n⏰ Eslatmalar: {r1}, {r2} va {r3} sanalarida yuboriladi.",
        reply_markup=main_menu()
    )


# ---------------------------
# TAKRORLASH VA TUGMALAR LOGIKASI
# ---------------------------
async def send_reviews(user_id):
    today_str = datetime.now().strftime("%Y-%m-%d")

    cursor.execute(
        "SELECT id, content FROM cards WHERE user_id=? AND (review_1=? OR review_2=? OR review_3=? OR extra_review=?)",
        (user_id, today_str, today_str, today_str, today_str)
    )
    rows = cursor.fetchall()

    if not rows:
        # Agar tugma orqali bosilgan bo'lsa xabar beramiz
        await bot.send_message(user_id, "📭 Bugun takrorlash uchun hech narsa yo'q.")
        return

    for row in rows:
        card_id, content = row
        await bot.send_message(
            user_id,
            f"📚 **Bugun takrorlash vaqti!**\n\n{content}",
            reply_markup=get_review_keyboard(card_id),
            parse_mode="Markdown"
        )
        # Bugun ko'rsatilgan 'extra_review' ni tozalaymiz
        cursor.execute("UPDATE cards SET extra_review=NULL WHERE id=? AND extra_review=?", (card_id, today_str))

    conn.commit()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith(('good_', 'bad_')))
async def process_callback(callback_query: types.CallbackQuery):
    action, card_id = callback_query.data.split("_")

    if action == "bad":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        cursor.execute("UPDATE cards SET extra_review=? WHERE id=?", (tomorrow, card_id))
        conn.commit()
        await callback_query.message.edit_text(
            f"❌ Qiyin bo'ldi. Ertaga yana eslataman!\n\n{callback_query.message.text}")
    else:
        await callback_query.message.edit_text(f"✅ Zo'r! O'rganishda davom eting.\n\n{callback_query.message.text}")

    await callback_query.answer()


# ---------------------------
# AVTOMATIK SCHEDULER
# ---------------------------
async def daily_scheduler():
    while True:
        cursor.execute("SELECT DISTINCT user_id FROM cards")
        users = cursor.fetchall()
        for user in users:
            try:
                # user bu tuple (user_id,) shaklida keladi
                await send_reviews(user[0])
            except Exception as e:
                logging.error(f"Xatolik: {e}")

        await asyncio.sleep(3600)  # Har 1 soatda tekshiradi


async def on_startup(_):
    asyncio.create_task(daily_scheduler())


# ---------------------------
# ISHGA TUSHIRISH
# ---------------------------
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)


