import logging
import sqlite3
import os
import random
import asyncio
import csv
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from gtts import gTTS
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor

# ---------------------------
# KONFIGURATSIYA
# ---------------------------
load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")  # Husanning Telegram user_id (string yoki int)
if ADMIN_ID:
    ADMIN_ID = int(ADMIN_ID)

if not API_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi. .env fayl yarating (.env.example'ga qarang).")

TZ = ZoneInfo("Asia/Tashkent")

# Husanning boshqa kanal/loyihalari — /start va promo-rotatsiyada ko'rsatiladi.
# Yangi loyiha chiqqanda shu ro'yxatga qo'shib qo'ying.
PROJECT_LINKS = [
    ("HusanAI — AI olami kanali", "https://t.me/AI_olami_1"),
]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Foydalanuvchi hozir bitta kartani tahrirlayaptimi — {user_id: card_id}.
# Xotirada saqlanadi (DB'da emas): bot qayta ishga tushsa tozalanadi, bu xavfsiz —
# foydalanuvchi shunchaki ✏️ tugmasini qayta bosadi.
pending_edit = {}

# ---------------------------
# DATABASE
# ---------------------------
db_path = os.getenv('DB_PATH', os.path.join(os.getcwd(), 'data.db'))
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TEXT,
    reminder_hour INTEGER DEFAULT 9,
    referred_by INTEGER,
    last_reminder_date TEXT,
    reviews_sent_count INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    content TEXT,
    ease_factor REAL DEFAULT 2.5,
    interval_days INTEGER DEFAULT 1,
    reps INTEGER DEFAULT 0,
    due_date TEXT,
    created_at TEXT,
    category TEXT DEFAULT 'other'
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS promo_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT,
    active INTEGER DEFAULT 1
)
""")
conn.commit()

# --- MIGRATSIYA: eski (category ustunisiz) bazalarga ustun qo'shish ---
# Eslatma: bu mavjud qatorlarni O'CHIRMAYDI — faqat yangi ustun qo'shiladi,
# eski kartalarning barchasi category='other' bilan davom etadi.
try:
    cursor.execute("ALTER TABLE cards ADD COLUMN category TEXT DEFAULT 'other'")
    conn.commit()
except sqlite3.OperationalError:
    pass  # ustun allaqachon mavjud


def ensure_user(user: types.User, referred_by=None):
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO users (user_id, username, first_name, joined_at, referred_by) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.username, user.first_name, datetime.now(TZ).isoformat(), referred_by)
        )
        conn.commit()
        return True  # yangi foydalanuvchi
    return False


def is_admin(user_id):
    return ADMIN_ID is not None and user_id == ADMIN_ID


# ---------------------------
# TUGMALAR (MENYU)
# ---------------------------

def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Yangi qo'shish"))
    kb.add(KeyboardButton("🔍 Bugun nima bor?"))
    kb.add(KeyboardButton("📊 Statistika"), KeyboardButton("📋 Kartalarim"))
    return kb


def get_review_keyboard(card_id, category="other"):
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(
        InlineKeyboardButton("🔴 Again", callback_data=f"rate_again_{card_id}"),
        InlineKeyboardButton("🟠 Hard", callback_data=f"rate_hard_{card_id}"),
        InlineKeyboardButton("🟢 Good", callback_data=f"rate_good_{card_id}"),
        InlineKeyboardButton("🔵 Easy", callback_data=f"rate_easy_{card_id}"),
    )
    # Talaffuz tugmasi FAQAT "til so'zi" deb belgilangan kartalarda ko'rinadi —
    # tarix/matematika va h.k. umumiy kartalarda chiqmaydi.
    if category == "lang":
        kb.add(InlineKeyboardButton("🔊 Talaffuz", callback_data=f"pronounce_{card_id}"))
    return kb


def get_card_keyboard(card_id, category="other"):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"edit_{card_id}"),
        InlineKeyboardButton("🗑 O'chirish", callback_data=f"del_{card_id}"),
    )
    if category == "lang":
        kb.add(InlineKeyboardButton("📚 Umumiy deb belgilash", callback_data=f"cat_other_{card_id}"))
    else:
        kb.add(InlineKeyboardButton("🗣 Til so'zi deb belgilash", callback_data=f"cat_lang_{card_id}"))
    return kb


def projects_text():
    lines = [f"• [{name}]({url})" for name, url in PROJECT_LINKS]
    return "\n".join(lines)


def extract_term(content):
    """Talaffuz uchun kartadan so'z/iborani ajratib olish.
    "abundant - mo'l-ko'l" kabi format bo'lsa, faqat "abundant" o'qiladi."""
    for sep in (" - ", " — ", " – ", ":", "\n"):
        if sep in content:
            return content.split(sep, 1)[0].strip()
    return content.strip()[:100]


# ---------------------------
# SM-2 (soddalashtirilgan) — takrorlash intervalini hisoblash
# ---------------------------

def apply_rating(ease_factor, interval_days, reps, rating):
    if rating == "again":
        reps = 0
        interval_days = 1
        ease_factor = max(1.3, ease_factor - 0.2)
    elif rating == "hard":
        reps += 1
        interval_days = max(1, round(interval_days * 1.2))
        ease_factor = max(1.3, ease_factor - 0.15)
    elif rating == "good":
        if reps == 0:
            interval_days = 1
        elif reps == 1:
            interval_days = 6
        else:
            interval_days = round(interval_days * ease_factor)
        reps += 1
    elif rating == "easy":
        interval_days = round(max(interval_days, 1) * ease_factor * 1.3)
        ease_factor = ease_factor + 0.15
        reps += 1
    due_date = (datetime.now(TZ) + timedelta(days=interval_days)).strftime("%Y-%m-%d")
    return ease_factor, interval_days, reps, due_date


# ---------------------------
# START KOMANDASI
# ---------------------------

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    args = message.get_args()
    referred_by = None
    if args and args.startswith("ref_"):
        try:
            candidate = int(args.replace("ref_", ""))
            if candidate != message.from_user.id:
                referred_by = candidate
        except ValueError:
            pass

    ensure_user(message.from_user, referred_by=referred_by)

    name = message.from_user.first_name
    intro_text = (
        f"Salom, {name}! 😊\n\n"
        f"Men *Spaced Repetition* (Interval takrorlash) botiman. 🧠\n\n"
        f"*Vazifam:* Siz o'rgangan yangi ma'lumotlarni unutilmas qilib xotirangizga muhrlash — til, IELTS so'zlari, konspekt, foydali fikr — nima bo'lishidan qat'iy nazar.\n\n"
        f"*Qanday ishlayman?*\n"
        f"1️⃣ Menga biror ma'lumot yuborasiz.\n"
        f"2️⃣ Men uni ilmiy asoslangan intervalda eslataman.\n"
        f"3️⃣ Har eslatmada qanchalik eslaganingizni belgilaysiz (Again/Hard/Good/Easy) — men shunga qarab keyingi vaqtni moslashtiraman.\n\n"
        f"Pastdagi menyu orqali boshlashingiz mumkin! 👇\n\n"
        f"📢 Mualliflik loyihalarim:\n{projects_text()}"
    )
    await message.reply(intro_text, reply_markup=main_menu(), parse_mode="Markdown", disable_web_page_preview=True)


# ---------------------------
# TUGMALAR LOGIKASI
# ---------------------------

@dp.message_handler(lambda message: message.text == "➕ Yangi qo'shish")
async def add_btn(message: types.Message):
    await message.reply("✍️ Marhamat, yangi o'rgangan ma'lumotingizni yozib yuboring:")


@dp.message_handler(lambda message: message.text == "🔍 Bugun nima bor?")
async def check_btn(message: types.Message):
    await send_reviews(message.from_user.id)


@dp.message_handler(lambda message: message.text == "📊 Statistika")
async def stats_btn(message: types.Message):
    await send_stats(message.from_user.id)


@dp.message_handler(lambda message: message.text == "📋 Kartalarim")
async def list_btn(message: types.Message):
    await send_card_list(message.from_user.id)


# ---------------------------
# STATISTIKA
# ---------------------------

async def send_stats(user_id):
    cursor.execute("SELECT COUNT(*) FROM cards WHERE user_id=?", (user_id,))
    total = cursor.fetchone()[0]

    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM cards WHERE user_id=? AND due_date<=?", (user_id, today_str))
    due_today = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM cards WHERE user_id=? AND reps>=3", (user_id,))
    mastered = cursor.fetchone()[0]

    text = (
        f"📊 *Statistikangiz*\n\n"
        f"Jami kartalar: {total}\n"
        f"Bugun/kechikkan takrorlash: {due_today}\n"
        f"Mustahkam o'rganilgan (3+ marta): {mastered}"
    )
    await bot.send_message(user_id, text, parse_mode="Markdown")


async def send_card_list(user_id, limit=15):
    cursor.execute(
        "SELECT id, content, due_date, category FROM cards WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    if not rows:
        await bot.send_message(user_id, "📭 Hali kartangiz yo'q. \"➕ Yangi qo'shish\" orqali qo'shing.")
        return

    await bot.send_message(user_id, f"📋 So'nggi {len(rows)} ta kartangiz:")
    for card_id, content, due_date, category in rows:
        preview = content if len(content) <= 200 else content[:200] + "…"
        label = "🗣 Til so'zi" if category == "lang" else "📚 Umumiy"
        await bot.send_message(
            user_id,
            f"🆔 {card_id} | 📅 keyingi: {due_date} | {label}\n{preview}",
            reply_markup=get_card_keyboard(card_id, category)
        )


# ---------------------------
# SAQLASH / TAHRIRLASH
# ---------------------------

RESERVED_TEXTS = ("➕", "🔍", "📊", "📋")


@dp.message_handler(lambda message: message.text and not message.text.startswith('/') and not message.text.startswith(RESERVED_TEXTS))
async def save_content(message: types.Message):
    user_id = message.from_user.id
    ensure_user(message.from_user)

    content = message.text

    # Agar foydalanuvchi ✏️ Tahrirlash tugmasini bosgan bo'lsa — YANGI karta
    # qo'shish o'rniga xuddi shu kartani yangilaymiz. Shu orqali eski (xato)
    # matn bazada boshqa qator bo'lib qolib, alohida eslatilib yurishining oldi olinadi.
    if user_id in pending_edit:
        card_id = pending_edit.pop(user_id)
        cursor.execute("SELECT category FROM cards WHERE id=? AND user_id=?", (card_id, user_id))
        row = cursor.fetchone()
        if row is None:
            await message.reply("⚠️ Bu karta topilmadi (o'chirilgan bo'lishi mumkin). Yangi karta sifatida saqlanmadi — qaytadan urinib ko'ring.")
            return
        category = row[0] or "other"
        cursor.execute("UPDATE cards SET content=? WHERE id=?", (content, card_id))
        conn.commit()
        await message.reply(
            f"✏️ Karta #{card_id} yangilandi! Eslatma jadvali (keyingi sana) o'zgarmadi.",
            reply_markup=main_menu()
        )
        await message.reply("Belgini tekshiring:", reply_markup=get_card_keyboard(card_id, category))
        return

    due_date = (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    created_at = datetime.now(TZ).isoformat()

    cursor.execute(
        "INSERT INTO cards (user_id, content, ease_factor, interval_days, reps, due_date, created_at, category) "
        "VALUES (?, ?, 2.5, 1, 0, ?, ?, 'other')",
        (user_id, content, due_date, created_at)
    )
    conn.commit()
    new_card_id = cursor.lastrowid

    await message.reply(
        f"✅ Muvaffaqiyatli saqlandi!\n\n⏰ Birinchi takrorlash: {due_date}",
        reply_markup=main_menu()
    )
    # Faqat chet tili so'zlariga talaffuz (🔊) tugmasi qo'shiladi — shuning uchun
    # har bir yangi kartadan turini so'raymiz. Javob bermasa "Umumiy" bo'lib qoladi.
    await message.reply(
        "Bu qanday ma'lumot? (faqat chet tili so'zlarida 🔊 talaffuz tugmasi chiqadi)",
        reply_markup=get_card_keyboard(new_card_id, "other")
    )


# ---------------------------
# TAKRORLASH YUBORISH
# ---------------------------

async def send_reviews(user_id):
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT id, content, category FROM cards WHERE user_id=? AND due_date<=? ORDER BY due_date ASC",
        (user_id, today_str)
    )
    rows = cursor.fetchall()

    if not rows:
        await bot.send_message(user_id, "📭 Hozircha takrorlash uchun ma'lumot yo'q. Yangi narsalar o'rganishda davom eting!")
        return

    for card_id, content, category in rows:
        await bot.send_message(
            user_id,
            f"📚 Takrorlash vaqti keldi!\n\n{content}",
            reply_markup=get_review_keyboard(card_id, category)
        )

    await maybe_send_promo(user_id)


async def maybe_send_promo(user_id):
    cursor.execute(
        "UPDATE users SET reviews_sent_count = reviews_sent_count + 1 WHERE user_id=?",
        (user_id,)
    )
    conn.commit()
    cursor.execute("SELECT reviews_sent_count FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    count = row[0] if row else 0

    if count % 5 != 0:
        return

    cursor.execute("SELECT text FROM promo_messages WHERE active=1")
    promos = cursor.fetchall()
    if not promos:
        return

    text = random.choice(promos)[0]
    await bot.send_message(user_id, f"💡 {text}", disable_web_page_preview=True)


# ---------------------------
# CALLBACK — BAHOLASH
# ---------------------------

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("rate_"))
async def process_rating(callback_query: types.CallbackQuery):
    _, rating, card_id = callback_query.data.split("_")

    cursor.execute("SELECT ease_factor, interval_days, reps FROM cards WHERE id=?", (card_id,))
    row = cursor.fetchone()
    if row is None:
        await callback_query.answer("Karta topilmadi.")
        return

    ease_factor, interval_days, reps = row
    ease_factor, interval_days, reps, due_date = apply_rating(ease_factor, interval_days, reps, rating)

    cursor.execute(
        "UPDATE cards SET ease_factor=?, interval_days=?, reps=?, due_date=? WHERE id=?",
        (ease_factor, interval_days, reps, due_date, card_id)
    )
    conn.commit()

    labels = {"again": "🔴 Yana ko'rasiz (ertaga)", "hard": "🟠 Qiyin bo'ldi",
              "good": "🟢 Yaxshi!", "easy": "🔵 Oson ekan!"}
    await callback_query.message.edit_text(f"{labels.get(rating, '')}\n📅 Keyingi: {due_date}")
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("del_"))
async def process_delete(callback_query: types.CallbackQuery):
    card_id = callback_query.data.split("_")[1]
    cursor.execute("DELETE FROM cards WHERE id=? AND user_id=?", (card_id, callback_query.from_user.id))
    conn.commit()
    pending_edit.pop(callback_query.from_user.id, None)
    await callback_query.message.edit_text("🗑 O'chirildi.")
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("edit_"))
async def process_edit_request(callback_query: types.CallbackQuery):
    card_id = callback_query.data.split("_", 1)[1]
    cursor.execute("SELECT id FROM cards WHERE id=? AND user_id=?", (card_id, callback_query.from_user.id))
    if cursor.fetchone() is None:
        await callback_query.answer("Karta topilmadi.")
        return
    pending_edit[callback_query.from_user.id] = int(card_id)
    await callback_query.answer()
    await callback_query.message.reply(
        f"✍️ #{card_id} uchun yangi matnni yuboring — shu karta yangilanadi, eskisi o'chib, yangisi yozilib qoladi."
    )


@dp.callback_query_handler(lambda c: c.data and (c.data.startswith("cat_lang_") or c.data.startswith("cat_other_")))
async def process_category_toggle(callback_query: types.CallbackQuery):
    is_lang = callback_query.data.startswith("cat_lang_")
    card_id = callback_query.data.split("_", 2)[2]
    new_category = "lang" if is_lang else "other"

    cursor.execute(
        "SELECT content, due_date FROM cards WHERE id=? AND user_id=?",
        (card_id, callback_query.from_user.id)
    )
    row = cursor.fetchone()
    if row is None:
        await callback_query.answer("Karta topilmadi.")
        return

    cursor.execute("UPDATE cards SET category=? WHERE id=?", (new_category, card_id))
    conn.commit()

    content, due_date = row
    preview = content if len(content) <= 200 else content[:200] + "…"
    label = "🗣 Til so'zi" if new_category == "lang" else "📚 Umumiy"
    try:
        await callback_query.message.edit_text(
            f"🆔 {card_id} | 📅 keyingi: {due_date} | {label}\n{preview}",
            reply_markup=get_card_keyboard(card_id, new_category)
        )
    except Exception:
        pass  # (masalan "saqlandi" xabari ustida bo'lsa, matn formatidan farq qilishi mumkin)
    await callback_query.answer("Belgi yangilandi.")


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("pronounce_"))
async def process_pronounce(callback_query: types.CallbackQuery):
    card_id = callback_query.data.split("_", 1)[1]
    cursor.execute(
        "SELECT content, category FROM cards WHERE id=? AND user_id=?",
        (card_id, callback_query.from_user.id)
    )
    row = cursor.fetchone()
    if row is None:
        await callback_query.answer("Karta topilmadi.")
        return

    content, category = row
    if category != "lang":
        await callback_query.answer(
            "🔊 Talaffuz faqat \"🗣 Til so'zi\" deb belgilangan kartalarda ishlaydi.",
            show_alert=True
        )
        return

    term = extract_term(content)
    await callback_query.answer("🔊 Tayyorlanmoqda...")
    try:
        buf = io.BytesIO()
        await asyncio.to_thread(gTTS(text=term, lang="en").write_to_fp, buf)
        buf.seek(0)
        await bot.send_audio(
            callback_query.from_user.id,
            types.InputFile(buf, filename="talaffuz.mp3"),
            title=term
        )
    except Exception as e:
        logging.error(f"TTS xatosi (karta {card_id}): {e}")
        await bot.send_message(callback_query.from_user.id, "❌ Talaffuzni tayyorlashda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")


# ---------------------------
# SOZLAMALAR / O'SISH KOMANDALARI
# ---------------------------

@dp.message_handler(commands=['vaqt'])
async def set_reminder_hour(message: types.Message):
    args = message.get_args().strip()
    if not args.isdigit() or not (0 <= int(args) <= 23):
        await message.reply("Foydalanish: /vaqt SOAT (masalan: /vaqt 9) — 0 dan 23 gacha, Toshkent vaqti bilan.")
        return
    hour = int(args)
    ensure_user(message.from_user)
    cursor.execute("UPDATE users SET reminder_hour=? WHERE user_id=?", (hour, message.from_user.id))
    conn.commit()
    await message.reply(f"⏰ Endi har kuni soat {hour}:00 (Toshkent vaqti) da eslataman.")


@dp.message_handler(commands=['invite'])
async def invite(message: types.Message):
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"
    await message.reply(f"👥 Do'stlaringizni taklif qiling:\n{link}")


@dp.message_handler(commands=['stats'])
async def stats_cmd(message: types.Message):
    await send_stats(message.from_user.id)


@dp.message_handler(commands=['list'])
async def list_cmd(message: types.Message):
    await send_card_list(message.from_user.id)


@dp.message_handler(commands=['broadcast'])
async def broadcast(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    text = message.get_args()
    if not text:
        await message.reply("Foydalanish: /broadcast Xabar matni")
        return

    cursor.execute("SELECT user_id FROM users")
    user_ids = [r[0] for r in cursor.fetchall()]
    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, disable_web_page_preview=True)
            sent += 1
        except Exception as e:
            failed += 1
            logging.warning(f"Broadcast xato ({uid}): {e}")
        await asyncio.sleep(0.05)  # Telegram rate-limit'ga tegmaslik uchun
    await message.reply(f"✅ Yuborildi: {sent} | ❌ Xato: {failed}")


@dp.message_handler(commands=['admin_stats'])
async def admin_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (f"{today_str}%",))
    new_today = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM cards")
    total_cards = cursor.fetchone()[0]
    await message.reply(
        f"👥 Jami foydalanuvchi: {total_users}\n"
        f"🆕 Bugun qo'shilgan: {new_today}\n"
        f"🗂 Jami karta: {total_cards}"
    )


@dp.message_handler(commands=['export'])
async def export_data(message: types.Message):
    if not is_admin(message.from_user.id):
        return  # admin bo'lmagan foydalanuvchiga hech narsa qaytarilmaydi

    # --- users.csv ---
    cursor.execute(
        "SELECT user_id, username, first_name, joined_at, reminder_hour, referred_by, reviews_sent_count FROM users ORDER BY joined_at"
    )
    users_rows = cursor.fetchall()

    users_buf = io.StringIO()
    writer = csv.writer(users_buf)
    writer.writerow(["user_id", "username", "first_name", "joined_at", "reminder_hour", "referred_by", "reviews_sent_count"])
    writer.writerows(users_rows)
    users_bytes = io.BytesIO(users_buf.getvalue().encode("utf-8-sig"))
    users_bytes.name = "users.csv"

    # --- cards.csv ---
    cursor.execute(
        "SELECT id, user_id, content, ease_factor, interval_days, reps, due_date, created_at, category FROM cards ORDER BY user_id, id"
    )
    cards_rows = cursor.fetchall()

    cards_buf = io.StringIO()
    writer = csv.writer(cards_buf)
    writer.writerow(["id", "user_id", "content", "ease_factor", "interval_days", "reps", "due_date", "created_at", "category"])
    writer.writerows(cards_rows)
    cards_bytes = io.BytesIO(cards_buf.getvalue().encode("utf-8-sig"))
    cards_bytes.name = "cards.csv"

    await message.reply(f"📤 Eksport: {len(users_rows)} foydalanuvchi, {len(cards_rows)} karta.")
    await bot.send_document(message.from_user.id, types.InputFile(users_bytes, filename="users.csv"))
    await bot.send_document(message.from_user.id, types.InputFile(cards_bytes, filename="cards.csv"))


# ---------------------------
# KUNLIK SCHEDULER (soatlik tekshiruv, drift yo'q)
# ---------------------------

async def hourly_scheduler():
    while True:
        now = datetime.now(TZ)
        current_hour = now.hour
        today_str = now.strftime("%Y-%m-%d")

        cursor.execute(
            "SELECT user_id FROM users WHERE reminder_hour=? AND (last_reminder_date IS NULL OR last_reminder_date!=?)",
            (current_hour, today_str)
        )
        due_users = [r[0] for r in cursor.fetchall()]

        for uid in due_users:
            try:
                await send_reviews(uid)
            except Exception as e:
                logging.error(f"Eslatma xatosi ({uid}): {e}")
            cursor.execute("UPDATE users SET last_reminder_date=? WHERE user_id=?", (today_str, uid))
            conn.commit()

        await asyncio.sleep(3600)  # har soatda tekshiradi


async def on_startup(_):
    asyncio.create_task(hourly_scheduler())


# ---------------------------
# RUN
# ---------------------------

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
