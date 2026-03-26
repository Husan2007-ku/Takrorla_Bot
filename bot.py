import logging
import sqlite3
import os
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor

# ---------------------------
# KONFIGURATSIYA
# ---------------------------
API_TOKEN = '8106728301:AAEq9OvTowwzbigPMCcAGfJVLqtO1UGmaJY'

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# ---------------------------
# DATABASE
# ---------------------------
db_path = os.path.join(os.getcwd(), 'data.db')
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    content TEXT,
    review_1 TEXT,
    review_2 TEXT,
    review_3 TEXT,
    extra_review TEXT,
    last_sent TEXT
)
""")
conn.commit()


# ---------------------------
# TUGMALAR (MENYU)
# ---------------------------

def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Yangi qo'shish"))
    kb.add(KeyboardButton("🔍 Bugun nima bor?"))
    return kb


def get_review_keyboard(card_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Esladim", callback_data=f"good_{card_id}"),
        InlineKeyboardButton("❌ Qiyin bo'ldi", callback_data=f"bad_{card_id}")
    )
    return kb


# ---------------------------
# START
# ---------------------------

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    name = message.from_user.first_name
    intro_text = (
        f"Salom, {name}! 😊\n\n"
        f"Men *Spaced Repetition* (Interval takrorlash) botiman. 🧠\n\n"
        f"*Vazifam:* Siz o'rgangan yangi ma'lumotlarni unutilmas qilib xotirangizga muhrlash.\n\n"
        f"*Qanday ishlayman?*\n"
        f"1️⃣ Siz menga biror ma'lumot yuborasiz.\n"
        f"2️⃣ Men uni 1 kundan keyin, 7 kundan keyin va 30 kundan keyin eslataman.\n"
        f"3️⃣ Agar 'Qiyin bo'ldi'ni bossangiz, ertaga yana so'rayman.\n\n"
        f"Pastdagi menyu orqali ishlashni boshlashingiz mumkin! 👇"
    )
    await message.reply(intro_text, reply_markup=main_menu(), parse_mode="Markdown")


# ---------------------------
# TUGMALAR LOGIKASI
# ---------------------------

@dp.message_handler(lambda message: message.text == "➕ Yangi qo'shish")
async def add_btn(message: types.Message):
    await message.reply("✍️ Marhamat, yangi o'rgangan ma'lumotingizni yozib yuboring:")


# FIX #1: Foydalanuvchi tugmani bosganda send_reviews chaqiriladi
# Lekin scheduler ham ishlamasligi uchun last_sent tekshiruvi ishlatiladi
@dp.message_handler(lambda message: message.text == "🔍 Bugun nima bor?")
async def check_btn(message: types.Message):
    await send_reviews(message.from_user.id)


# ---------------------------
# SAQLASH
# ---------------------------

# FIX #2: Yangi qo'shish rejimida foydalanuvchi matnini to'g'ri filter qilish
# "➕ Yangi qo'shish" bosilgandan keyin keyingi xabar saqlansin deb
# state yoki oddiy lambda filter ishlatamiz
user_adding = set()  # Qaysi userlar hozir ma'lumot qo'shmoqda


@dp.message_handler(lambda message: message.text == "➕ Yangi qo'shish")
async def add_btn(message: types.Message):
    user_adding.add(message.from_user.id)
    await message.reply("✍️ Marhamat, yangi o'rgangan ma'lumotingizni yozib yuboring:")


@dp.message_handler(lambda message: (
        not message.text.startswith(('/', '➕', '🔍')) and
        message.from_user.id in user_adding
))
async def save_content(message: types.Message):
    user_id = message.from_user.id
    content = message.text

    # FIX #3: user_adding setidan olib tashla (bir marta saqlansin)
    user_adding.discard(user_id)

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
        f"✅ Muvaffaqiyatli saqlandi!\n\n"
        f"⏰ Sizga buni quyidagi sanalarda eslataman:\n"
        f"📅 1-takrorlash: {r1}\n"
        f"📅 2-takrorlash: {r2}\n"
        f"📅 3-takrorlash: {r3}",
        reply_markup=main_menu()
    )


# ---------------------------
# TAKRORLASH FUNKSIYASI
# ---------------------------

async def send_reviews(user_id):
    today_str = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        """SELECT id, content FROM cards 
           WHERE user_id=? 
           AND (review_1=? OR review_2=? OR review_3=? OR extra_review=?) 
           AND (last_sent IS NULL OR last_sent!=?)""",
        (user_id, today_str, today_str, today_str, today_str, today_str)
    )
    rows = cursor.fetchall()

    if not rows:
        await bot.send_message(
            user_id,
            "📭 Bugun takrorlash uchun ma'lumotlar yo'q. Yangi narsalar o'rganishda davom eting!"
        )
        return

    for row in rows:
        card_id, content = row

        # FIX #4: Har xabar yuborishdan OLDIN last_sent ni yangilaymiz
        # Shunda agar xato bo'lsa ham ikki marta yuborilmaydi
        cursor.execute(
            "UPDATE cards SET last_sent=? WHERE id=?",
            (today_str, card_id)
        )
        conn.commit()  # FIX #5: Har biridan keyin commit (loop ichida)

        await bot.send_message(
            user_id,
            f"📚 Takrorlash vaqti keldi!\n\n{content}",
            reply_markup=get_review_keyboard(card_id)
        )


# ---------------------------
# CALLBACK (Esladim / Qiyin bo'ldi)
# ---------------------------

@dp.callback_query_handler(lambda c: c.data and c.data.startswith(('good_', 'bad_')))
async def process_callback(callback_query: types.CallbackQuery):
    data_parts = callback_query.data.split("_")
    action = data_parts[0]
    card_id = data_parts[1]

    if action == "bad":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        cursor.execute(
            "UPDATE cards SET extra_review=? WHERE id=?",
            (tomorrow, card_id)
        )
        conn.commit()
        await callback_query.message.edit_text("❌ Qiyin bo'ldi. Ertaga yana chiqadi!")
    else:
        await callback_query.message.edit_text("✅ Ajoyib! Bilimingiz mustahkamlanmoqda.")

    await callback_query.answer()


# ---------------------------
# SCHEDULER — FIX #6: Asosiy tuzatish
# Har restart da xabar yuborilmasin
# Faqat soat 09:00 da yuborsin
# ---------------------------

async def daily_scheduler():
    logging.info("Scheduler ishga tushdi.")

    while True:
        now = datetime.now()

        # Faqat soat 09:00 da ishlaydi (0-60 soniya oraliqda)
        if now.hour == 9 and now.minute == 0:
            logging.info("Kunlik eslatmalar yuborilmoqda...")

            cursor.execute("SELECT DISTINCT user_id FROM cards")
            users = cursor.fetchall()

            for user in users:
                try:
                    await send_reviews(user[0])
                except Exception as e:
                    logging.error(f"Xatolik user {user[0]}: {e}")

            # Bir soat kutib, keyin yana tekshirsin (bir kunda 2 marta ishlamasin)
            await asyncio.sleep(3600)
        else:
            # Har 60 soniyada vaqtni tekshiradi
            await asyncio.sleep(60)


async def on_startup(_):
    # FIX #7: create_task to'g'ri ishlatilishi
    asyncio.ensure_future(daily_scheduler())
    logging.info("Bot ishga tushdi!")


# ---------------------------
# RUN
# ---------------------------

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)



