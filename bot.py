import logging
import sqlite3
import os
import random
import asyncio
import csv
import io
import json
import aiohttp
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from gtts import gTTS
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor
from aiogram.utils.exceptions import BotBlocked, UserDeactivated, ChatNotFound

# Foydalanuvchi botni bloklagani/akkountini o'chirgani aniqlanadigan xatolar —
# shu turdagi xato kelsa, xabar yubormay qo'yish o'rniga is_blocked=1 deb belgilaymiz.
BLOCKED_EXCEPTIONS = (BotBlocked, UserDeactivated, ChatNotFound)

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

# --- AI Test funksiyasi (referral orqali ochiladigan) ---
# Ikkala provider uchun ham alohida key saqlanadi — shunda AI_PROVIDER'ni
# almashtirish uchun keylarni qayta kiritish shart emas.
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").strip().lower()  # "groq" | "gemini"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
# Groq modellari tez-tez eskirib/o'chirilib turadi (masalan llama-3.1-8b-instant 2026-yilda
# o'chirilgan). Asosiy model "model_not_found" bersa, shu ro'yxatdagi keyingisi avtomatik sinaladi.
GROQ_FALLBACK_MODELS = ["llama-3.3-70b-versatile", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]
REFERRAL_MILESTONE_SIZE = 3      # necha ta FAOL taklif = 1 ochilish
AI_ACCESS_DAYS_PER_MILESTONE = 30  # har ochilishda necha kunlik AI Test kirish beriladi
AI_PROMPT_MAX_CHARS = 800  # juda uzun karta (masalan ko'chirib tashlangan katta matn) AI'ni chalkashtirmasin

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

# Foydalanuvchi hozir AI Test jarayonidami — {user_id: {"queue": [...], "current": {...}, "correct": int, "total": int}}.
# Xotirada saqlanadi: bot qayta ishga tushsa test to'xtaydi, foydalanuvchi qayta boshlaydi (xavfsiz, DB'ga ta'sir qilmaydi).
active_ai_tests = {}

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
    reviews_sent_count INTEGER DEFAULT 0,
    ai_access_until TEXT,
    ai_milestones_granted INTEGER DEFAULT 0,
    is_blocked INTEGER DEFAULT 0
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

# /broadcast orqali kimga, qaysi xabar (message_id) yuborilganini saqlaydi —
# xato ketsa /undo_broadcast bilan bekor qilish (o'chirish) uchun kerak.
cursor.execute("""
CREATE TABLE IF NOT EXISTS broadcast_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_id TEXT,
    user_id INTEGER,
    message_id INTEGER,
    sent_at TEXT
)
""")
conn.commit()

# --- MIGRATSIYA: eski bazalarga yangi ustunlarni qo'shish ---
# Eslatma: bular mavjud qatorlarni O'CHIRMAYDI — faqat yangi ustun qo'shiladi,
# eski foydalanuvchi/kartalar o'zgarishsiz qoladi (default qiymatlar bilan).
for alter_sql in (
    "ALTER TABLE cards ADD COLUMN category TEXT DEFAULT 'other'",
    "ALTER TABLE users ADD COLUMN ai_access_until TEXT",
    "ALTER TABLE users ADD COLUMN ai_milestones_granted INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0",
):
    try:
        cursor.execute(alter_sql)
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
    # Foydalanuvchi bizga xabar yozgani — demak botni bloklamagan. Oldin bloklangan
    # deb belgilangan bo'lsa, shu yerda tozalaymiz (faqat kerak bo'lsagina yozamiz).
    cursor.execute("UPDATE users SET is_blocked=0 WHERE user_id=? AND is_blocked=1", (user.id,))
    if cursor.rowcount:
        conn.commit()
    return False


def is_admin(user_id):
    return ADMIN_ID is not None and user_id == ADMIN_ID


def mark_user_blocked(user_id, blocked=True):
    cursor.execute("UPDATE users SET is_blocked=? WHERE user_id=?", (1 if blocked else 0, user_id))
    conn.commit()


# ---------------------------
# REFERRAL → AI TEST OCHILISHI
# ---------------------------

def count_activated_referrals(referrer_id):
    """Referrer taklif qilgan, /start bosib KAMIDA 1 TA karta qo'shgan (ya'ni "faol") foydalanuvchilar soni."""
    cursor.execute(
        "SELECT COUNT(DISTINCT u.user_id) FROM users u "
        "WHERE u.referred_by=? AND EXISTS (SELECT 1 FROM cards c WHERE c.user_id=u.user_id)",
        (referrer_id,)
    )
    return cursor.fetchone()[0]


def has_ai_access(user_id):
    if is_admin(user_id):
        return True  # admin (Husan) uchun AI Test har doim ochiq — referral talab qilinmaydi
    cursor.execute("SELECT ai_access_until FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row or not row[0]:
        return False
    try:
        return datetime.fromisoformat(row[0]) > datetime.now(TZ)
    except ValueError:
        return False


async def check_and_grant_ai_access(referrer_id):
    """Har REFERRAL_MILESTONE_SIZE ta yangi faol taklifga AI_ACCESS_DAYS_PER_MILESTONE kunlik
    AI Test kirishi beriladi (mavjud muddat ustiga qo'shiladi). Idempotent — ortiqcha chaqirilsa
    ham qayta mukofot bermaydi, chunki ai_milestones_granted allaqachon hisoblangan ulushni saqlaydi."""
    if referrer_id is None:
        return

    cursor.execute("SELECT ai_milestones_granted, ai_access_until FROM users WHERE user_id=?", (referrer_id,))
    row = cursor.fetchone()
    if row is None:
        return
    granted, access_until_str = row
    granted = granted or 0

    activated_count = count_activated_referrals(referrer_id)
    earned = activated_count // REFERRAL_MILESTONE_SIZE
    new_milestones = earned - granted
    if new_milestones <= 0:
        return

    now = datetime.now(TZ)
    base = now
    if access_until_str:
        try:
            existing = datetime.fromisoformat(access_until_str)
            if existing > now:
                base = existing
        except ValueError:
            pass
    new_until = base + timedelta(days=AI_ACCESS_DAYS_PER_MILESTONE * new_milestones)

    cursor.execute(
        "UPDATE users SET ai_milestones_granted=?, ai_access_until=? WHERE user_id=?",
        (earned, new_until.isoformat(), referrer_id)
    )
    conn.commit()

    try:
        await bot.send_message(
            referrer_id,
            f"🎉 Tabriklaymiz! Sizda hozir {activated_count} ta faol taklif bor.\n"
            f"🧠 AI Test funksiyasi {new_until.strftime('%Y-%m-%d')} sanagacha ochildi!\n"
            f"Boshlash uchun pastdagi \"🧠 AI Test\" tugmasi yoki /aitest."
        )
    except BLOCKED_EXCEPTIONS:
        mark_user_blocked(referrer_id, True)
    except Exception as e:
        logging.warning(f"AI-ochilish xabari yuborilmadi ({referrer_id}): {e}")


async def ask_ai(system_prompt: str, user_prompt: str) -> str:
    """Groq yoki Gemini'ga (AI_PROVIDER orqali tanlanadi) so'rov yuboradi va matn javobini qaytaradi."""
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if AI_PROVIDER == "gemini":
            if not GEMINI_API_KEY:
                raise RuntimeError("GEMINI_API_KEY sozlanmagan (.env'ga qarang).")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
            payload = {"contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}]}
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"Gemini xato ({resp.status}): {data}")
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:  # groq — OpenAI-compatible API
            if not GROQ_API_KEY:
                raise RuntimeError("GROQ_API_KEY sozlanmagan (.env'ga qarang).")
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            # Asosiy model + fallback'lar (dublikatsiz, tartib saqlangan holda)
            models_to_try = [GROQ_MODEL] + [m for m in GROQ_FALLBACK_MODELS if m != GROQ_MODEL]
            last_error = None
            for model in models_to_try:
                payload = {"model": model, "messages": messages, "temperature": 0.4, "max_tokens": 300}
                async with session.post(url, json=payload, headers=headers) as resp:
                    data = await resp.json()
                    if resp.status == 200:
                        return data["choices"][0]["message"]["content"].strip()
                    last_error = RuntimeError(f"Groq xato ({resp.status}, model={model}): {data}")
                    error_code = (data.get("error") or {}).get("code")
                    if error_code != "model_not_found":
                        raise last_error  # boshqa turdagi xato (masalan noto'g'ri key) — darhol to'xtatamiz
                    logging.warning(f"Groq modeli topilmadi ({model}), keyingi fallback sinaladi...")
            raise last_error


# ---------------------------
# TUGMALAR (MENYU)
# ---------------------------

def main_menu(user_id=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Yangi qo'shish"))
    kb.add(KeyboardButton("🔍 Bugun nima bor?"))
    kb.add(KeyboardButton("📊 Statistika"), KeyboardButton("📋 Kartalarim"))
    if user_id is not None and has_ai_access(user_id):
        kb.add(KeyboardButton("🧠 AI Test"))
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


def truncate_for_ai(content):
    """AI promptiga yuboriladigan matnni cheklaydi — juda uzun karta (masalan ko'p xabar
    ko'chirib tashlangan) AI'ni chalkashtirib, bo'sh/g'alati javob qaytarishining oldini oladi."""
    content = content.strip()
    if len(content) <= AI_PROMPT_MAX_CHARS:
        return content
    return content[:AI_PROMPT_MAX_CHARS] + "..."


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
    await message.reply(intro_text, reply_markup=main_menu(message.from_user.id), parse_mode="Markdown", disable_web_page_preview=True)


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


@dp.message_handler(lambda message: message.text == "🧠 AI Test")
async def ai_test_btn(message: types.Message):
    await start_ai_test(message.from_user.id)


@dp.message_handler(commands=['aitest'])
async def ai_test_cmd(message: types.Message):
    await start_ai_test(message.from_user.id)


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
# AI TEST (faqat 3 ta faol taklifdan keyin ochiladi)
# ---------------------------

AI_TEST_MODES = {
    "quiz": "📝 Quiz (variantli savollar)",
    "card": "🎴 Kartochka (o'zim eslayman, keyin javobni ochaman)",
    "written": "✍️ Yozma (o'z so'zim bilan javob beraman, AI baholaydi)",
}


def ai_mode_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    for mode, label in AI_TEST_MODES.items():
        kb.add(InlineKeyboardButton(label, callback_data=f"aimode_{mode}"))
    return kb


def quiz_keyboard(options):
    kb = InlineKeyboardMarkup(row_width=1)
    letters = ["A", "B", "C", "D"]
    for i, opt in enumerate(options):
        kb.add(InlineKeyboardButton(f"{letters[i]}) {opt}", callback_data=f"quizans_{i}"))
    return kb


async def start_ai_test(user_id):
    if not has_ai_access(user_id):
        activated = count_activated_referrals(user_id)
        remaining = REFERRAL_MILESTONE_SIZE - (activated % REFERRAL_MILESTONE_SIZE)
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
        await bot.send_message(
            user_id,
            f"🔒 AI Test hozircha yopiq.\n\n"
            f"Ochish uchun {REFERRAL_MILESTONE_SIZE} kishini taklif qiling — ular /start bosib, "
            f"kamida 1 ta ma'lumot qo'shishi kerak (shundagina \"faol\" hisoblanadi).\n\n"
            f"✅ Hozirgi faol takliflaringiz: {activated}\n"
            f"⏳ Keyingi ochilishgacha: {remaining} kishi\n\n"
            f"🔗 Taklif havolangiz:\n{link}"
        )
        return

    cursor.execute("SELECT COUNT(*) FROM cards WHERE user_id=?", (user_id,))
    if cursor.fetchone()[0] == 0:
        await bot.send_message(user_id, "📭 Hali kartangiz yo'q. Avval \"➕ Yangi qo'shish\" orqali ma'lumot kiriting, keyin test o'tkazamiz.")
        return

    await bot.send_message(
        user_id,
        "🧠 O'rgangan narsalaringizni mustahkamlashda 3 usulda yordam bera olaman:\n\n"
        "📝 Quiz — variantli savollar (A/B/C/D dan birini tanlaysiz)\n"
        "🎴 Kartochka — ipuchi beraman, o'zingiz eslaysiz, keyin javobni ochaman\n"
        "✍️ Yozma — savolga o'z so'zingiz bilan yozma javob berasiz, AI baholaydi\n\n"
        "Qaysi birini xohlaysiz?",
        reply_markup=ai_mode_keyboard()
    )


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("aimode_"))
async def process_ai_mode_choice(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    mode = callback_query.data.split("_", 1)[1]
    if mode not in AI_TEST_MODES:
        await callback_query.answer("Noma'lum usul.")
        return

    cursor.execute("SELECT id, content FROM cards WHERE user_id=? ORDER BY RANDOM() LIMIT 5", (user_id,))
    rows = cursor.fetchall()
    if not rows:
        await callback_query.answer("Kartangiz topilmadi.")
        return

    active_ai_tests[user_id] = {"mode": mode, "queue": rows, "current": None, "correct": 0, "total": len(rows)}
    await callback_query.message.edit_text(f"{AI_TEST_MODES[mode]} tanlandi. {len(rows)} ta savol bo'ladi.")
    await callback_query.answer()
    await send_next_ai_question(user_id)


async def send_next_ai_question(user_id):
    """Har uchala rejim uchun ham navbatdagi kartani tanlab, tegishli funksiyaga yo'naltiradi."""
    state = active_ai_tests.get(user_id)
    if not state:
        return
    if not state["queue"]:
        await bot.send_message(user_id, f"✅ Test tugadi! Natija: {state['correct']}/{state['total']}")
        active_ai_tests.pop(user_id, None)
        return

    card_id, content = state["queue"].pop(0)
    mode = state["mode"]
    if mode == "quiz":
        await send_quiz_question(user_id, card_id, content)
    elif mode == "card":
        await send_flashcard_question(user_id, card_id, content)
    else:
        await send_written_question(user_id, card_id, content)


# --- ✍️ Yozma rejim ---

async def send_written_question(user_id, card_id, content):
    state = active_ai_tests[user_id]
    state["current"] = {"id": card_id, "content": content}
    try:
        question = await ask_ai(
            "Sen o'zbek tilida ishlaydigan ta'lim yordamchisisan. Senga foydalanuvchi eslab qolmoqchi bo'lgan "
            "bitta ma'lumot beriladi. Shu ma'lumot asosida uning yodda saqlaganini tekshiradigan QISQA (1 gap) "
            "savol tuz. Javobning o'zini oshkor qilma. Faqat savol matnini yoz, boshqa hech narsa qo'shma.",
            f"Ma'lumot: {truncate_for_ai(content)}"
        )
        question = (question or "").strip()
        if not question:
            raise ValueError("AI bo'sh javob qaytardi")
    except Exception as e:
        logging.error(f"AI savol yaratishda xato (karta {card_id}): {e}")
        question = f"Quyidagi ma'lumotni o'z so'zlaringiz bilan tushuntirib bering:\n{content}"

    await bot.send_message(user_id, f"❓ {question}")


async def handle_ai_test_answer(message: types.Message):
    user_id = message.from_user.id
    state = active_ai_tests[user_id]
    current = state["current"]
    state["current"] = None
    user_answer = message.text

    try:
        evaluation = await ask_ai(
            "Sen o'zbek tilida ishlaydigan mehribon o'qituvchisan. Foydalanuvchiga savol berilgan edi, u javob yozdi. "
            "Asl ma'lumot bilan solishtirib bahola va 2-3 gapda o'zbek tilida qisqa fikr-mulohaza yoz. "
            "Javobingni albatta '✅ To'g'ri' yoki '❌ Noto'g'ri' bilan boshla.",
            f"Asl ma'lumot: {truncate_for_ai(current['content'])}\nFoydalanuvchi javobi: {user_answer}"
        )
    except Exception as e:
        logging.error(f"AI baholashda xato (karta {current['id']}): {e}")
        evaluation = f"⚠️ AI bahosi olinmadi.\nTo'g'ri ma'lumot: {current['content']}"

    if evaluation.strip().startswith("✅"):
        state["correct"] += 1

    await message.reply(evaluation)
    await send_next_ai_question(user_id)


# --- 📝 Quiz rejimi ---

async def generate_quiz_question(content):
    """AI'dan qat'iy JSON formatida 4 variantli savol so'raydi.
    Format buzilgan bo'lsa None qaytaradi — chaqiruvchi yozma rejimga fallback qiladi."""
    try:
        raw = await ask_ai(
            "Sen o'zbek tilida test tuzuvchi yordamchisan. Senga ma'lumot beriladi. Shu ma'lumot asosida "
            "4 variantli (faqat BITTA to'g'ri, qolgan 3 tasi mantiqan yaqin lekin noto'g'ri) savol tuz. "
            "FAQAT quyidagi JSON formatida javob qaytar, boshqa hech qanday matn, izoh yoki ``` belgisi qo'shma:\n"
            '{"question": "...", "options": ["...", "...", "...", "..."], "correct_index": 0}',
            f"Ma'lumot: {truncate_for_ai(content)}"
        )
        raw = raw.strip().strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        question_text = str(data.get("question") or "").strip()
        options = data["options"]
        correct_index = int(data["correct_index"])
        if not question_text or not isinstance(options, list) or len(options) != 4 or not (0 <= correct_index < 4):
            return None
        options = [str(o).strip() for o in options]
        if any(not o for o in options):
            return None
        return {"question": question_text, "options": options, "correct_index": correct_index}
    except Exception as e:
        logging.error(f"Quiz JSON parse xatosi: {e}")
        return None


async def send_quiz_question(user_id, card_id, content):
    quiz = await generate_quiz_question(content)
    if quiz is None:
        # AI to'g'ri JSON qaytarmadi — foydalanuvchi testi uzilib qolmasin, shu savolni yozma rejimda beramiz.
        await send_written_question(user_id, card_id, content)
        return

    state = active_ai_tests[user_id]
    state["current"] = {
        "id": card_id,
        "content": content,
        "correct_index": quiz["correct_index"],
        "options": quiz["options"],
    }
    await bot.send_message(user_id, f"📝 {quiz['question']}", reply_markup=quiz_keyboard(quiz["options"]))


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("quizans_"))
async def process_quiz_answer(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    state = active_ai_tests.get(user_id)
    if not state or not state.get("current"):
        await callback_query.answer("Bu test allaqachon tugagan.")
        return

    chosen_index = int(callback_query.data.split("_", 1)[1])
    current = state.pop("current")
    correct_index = current["correct_index"]
    options = current["options"]
    letters = ["A", "B", "C", "D"]

    if chosen_index == correct_index:
        state["correct"] += 1
        result_text = f"✅ To'g'ri! Javob: {letters[correct_index]}) {options[correct_index]}"
    else:
        result_text = (
            f"❌ Noto'g'ri. Siz tanladingiz: {letters[chosen_index]}) {options[chosen_index]}\n"
            f"To'g'ri javob: {letters[correct_index]}) {options[correct_index]}"
        )

    await callback_query.message.edit_text(result_text)
    await callback_query.answer()
    await send_next_ai_question(user_id)


# --- 🎴 Kartochka rejimi ---

async def send_flashcard_question(user_id, card_id, content):
    try:
        hint = await ask_ai(
            "Sen o'zbek tilida ishlaydigan ta'lim yordamchisisan. Senga ma'lumot beriladi. Shu ma'lumotni "
            "ESLAB QOLISHNI tekshiradigan QISQA (1 gap) ipuchi/savol yoz. Javobning o'zini yozma. "
            "Faqat shu ipuchi matnini yoz, boshqa hech narsa qo'shma.",
            f"Ma'lumot: {truncate_for_ai(content)}"
        )
        hint = (hint or "").strip()
        if not hint:
            raise ValueError("AI bo'sh javob qaytardi")
    except Exception as e:
        logging.error(f"Kartochka ipuchi xatosi (karta {card_id}): {e}")
        hint = "Bu ma'lumotni eslay olasizmi?"

    state = active_ai_tests[user_id]
    state["current"] = {"id": card_id, "content": content}
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("👁 Javobni ko'rsatish", callback_data="flip_card"))
    await bot.send_message(user_id, f"🎴 {hint}", reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data == "flip_card")
async def process_flip_card(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    state = active_ai_tests.get(user_id)
    if not state or not state.get("current"):
        await callback_query.answer("Bu test allaqachon tugagan.")
        return

    content = state["current"]["content"]
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Bilardim", callback_data="selfrate_yes"),
        InlineKeyboardButton("❌ Bilmadim", callback_data="selfrate_no"),
    )
    await callback_query.message.edit_text(f"📖 Javob: {content}", reply_markup=kb)
    await callback_query.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("selfrate_"))
async def process_selfrate(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    state = active_ai_tests.get(user_id)
    if not state or not state.get("current"):
        await callback_query.answer("Bu test allaqachon tugagan.")
        return

    knew_it = callback_query.data.endswith("_yes")
    state.pop("current")
    if knew_it:
        state["correct"] += 1

    await callback_query.message.edit_text("✅ Belgilandi." if knew_it else "❌ Belgilandi — yana takrorlang.")
    await callback_query.answer()
    await send_next_ai_question(user_id)


# ---------------------------
# SAQLASH / TAHRIRLASH
# ---------------------------

RESERVED_TEXTS = ("➕", "🔍", "📊", "📋", "🧠")


@dp.message_handler(lambda message: message.text and not message.text.startswith('/') and not message.text.startswith(RESERVED_TEXTS))
async def save_content(message: types.Message):
    user_id = message.from_user.id
    ensure_user(message.from_user)

    content = message.text

    # Foydalanuvchi hozir "✍️ Yozma" AI Test rejimida savolga javob bermoqchi bo'lsa —
    # bu matnni yangi karta sifatida SAQLAMAYMIZ, balki test javobi sifatida qayta ishlaymiz.
    # (Quiz/Kartochka rejimlari tugma orqali ishlaydi, matn kutmaydi.)
    active_test = active_ai_tests.get(user_id)
    if active_test and active_test.get("mode") == "written" and active_test.get("current"):
        await handle_ai_test_answer(message)
        return

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
            reply_markup=main_menu(user_id)
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
        reply_markup=main_menu(user_id)
    )
    # Faqat chet tili so'zlariga talaffuz (🔊) tugmasi qo'shiladi — shuning uchun
    # har bir yangi kartadan turini so'raymiz. Javob bermasa "Umumiy" bo'lib qoladi.
    await message.reply(
        "Bu qanday ma'lumot? (faqat chet tili so'zlarida 🔊 talaffuz tugmasi chiqadi)",
        reply_markup=get_card_keyboard(new_card_id, "other")
    )

    # "Faol taklif" hodisasi — bu foydalanuvchining ENG BIRINCHI kartasimi tekshiramiz.
    # Agar shu odam kimningdir referral havolasi orqali kelgan bo'lsa va shu o'zining
    # birinchi kartasi bo'lsa — bu taklif "faollashdi", taklif qilgan odamning
    # AI Test hisobini yangilaymiz (har 3 taga 30 kun ochiladi).
    cursor.execute("SELECT COUNT(*) FROM cards WHERE user_id=?", (user_id,))
    total_cards = cursor.fetchone()[0]
    if total_cards == 1:
        cursor.execute("SELECT referred_by FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        referred_by = row[0] if row else None
        if referred_by:
            await check_and_grant_ai_access(referred_by)


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
        try:
            await bot.send_message(
                user_id,
                f"📚 Takrorlash vaqti keldi!\n\n{content}",
                reply_markup=get_review_keyboard(card_id, category)
            )
        except BLOCKED_EXCEPTIONS:
            mark_user_blocked(user_id, True)
            return  # bloklagan foydalanuvchiga qolgan kartalarni ham yuborishga urinmaymiz

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
    user_id = message.from_user.id
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    activated = count_activated_referrals(user_id)
    remaining = REFERRAL_MILESTONE_SIZE - (activated % REFERRAL_MILESTONE_SIZE)
    await message.reply(
        f"👥 Do'stlaringizni taklif qiling:\n{link}\n\n"
        f"✅ Faol takliflaringiz: {activated} (ular /start bosib, kamida 1 ta ma'lumot qo'shgan bo'lishi kerak)\n"
        f"🧠 Har {REFERRAL_MILESTONE_SIZE} ta faol taklifga {AI_ACCESS_DAYS_PER_MILESTONE} kunlik AI Test ochiladi — "
        f"keyingisigacha {remaining} kishi qoldi."
    )


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

    # Telegram baribir 48 soatdan eski xabarni o'chirishga ruxsat bermaydi —
    # shuning uchun jadvalni cheksiz kattalashtirmaslik uchun eskilarini tozalaymiz.
    cutoff = (datetime.now(TZ) - timedelta(hours=48)).isoformat()
    cursor.execute("DELETE FROM broadcast_log WHERE sent_at < ?", (cutoff,))
    conn.commit()

    broadcast_id = datetime.now(TZ).strftime("%Y%m%d%H%M%S")
    sent_at = datetime.now(TZ).isoformat()
    cursor.execute("SELECT user_id FROM users")
    user_ids = [r[0] for r in cursor.fetchall()]
    sent, blocked, failed = 0, 0, 0
    for uid in user_ids:
        try:
            sent_msg = await bot.send_message(uid, text, disable_web_page_preview=True)
        except BLOCKED_EXCEPTIONS:
            mark_user_blocked(uid, True)
            blocked += 1
        except Exception as e:
            failed += 1
            logging.warning(f"Broadcast xato ({uid}): {e}")
        else:
            cursor.execute(
                "INSERT INTO broadcast_log (broadcast_id, user_id, message_id, sent_at) VALUES (?, ?, ?, ?)",
                (broadcast_id, uid, sent_msg.message_id, sent_at)
            )
            sent += 1
        await asyncio.sleep(0.05)  # Telegram rate-limit'ga tegmaslik uchun
    conn.commit()
    await message.reply(
        f"✅ Yuborildi: {sent} | 🚫 Bloklagan: {blocked} | ❌ Boshqa xato: {failed}\n"
        f"🆔 ID: {broadcast_id}\n"
        f"Xato ketsa (48 soat ichida): /undo_broadcast {broadcast_id}"
    )


@dp.message_handler(commands=['undo_broadcast'])
async def undo_broadcast(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    broadcast_id = message.get_args().strip()
    if not broadcast_id:
        cursor.execute("SELECT broadcast_id FROM broadcast_log ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if row is None:
            await message.reply("Bekor qilinadigan broadcast topilmadi (yo yuborilmagan, yo 48 soatdan oshgan).")
            return
        broadcast_id = row[0]

    cursor.execute("SELECT user_id, message_id FROM broadcast_log WHERE broadcast_id=?", (broadcast_id,))
    rows = cursor.fetchall()
    if not rows:
        await message.reply(f"'{broadcast_id}' ID'li broadcast topilmadi.")
        return

    deleted, failed = 0, 0
    for uid, msg_id in rows:
        try:
            await bot.delete_message(uid, msg_id)
            deleted += 1
        except Exception as e:
            failed += 1
            logging.warning(f"O'chirib bo'lmadi ({uid}, msg {msg_id}): {e}")
        await asyncio.sleep(0.05)

    cursor.execute("DELETE FROM broadcast_log WHERE broadcast_id=?", (broadcast_id,))
    conn.commit()
    await message.reply(
        f"🗑 Bekor qilindi: {deleted} | ❌ O'chmadi: {failed}\n"
        f"(Telegram faqat 48 soat ichidagi xabarlarni o'chirtiradi — shundan oshgani \"O'chmadi\"ga tushadi)"
    )


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
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1")
    blocked_count = cursor.fetchone()[0]
    await message.reply(
        f"👥 Jami foydalanuvchi: {total_users}\n"
        f"🆕 Bugun qo'shilgan: {new_today}\n"
        f"🚫 Botni bloklaganlar: {blocked_count}\n"
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


async def backfill_ai_access():
    """Bot birinchi marta shu (AI Test) versiyada ishga tushganda, ALLAQACHON 3+ faol
    taklifga ega bo'lgan foydalanuvchilarni bir martalik tekshirib, ularga ham
    AI Test kirishini ochib beradi (deploy vaqtidagi adolat uchun)."""
    cursor.execute("SELECT DISTINCT referred_by FROM users WHERE referred_by IS NOT NULL")
    referrer_ids = [r[0] for r in cursor.fetchall()]
    for referrer_id in referrer_ids:
        try:
            await check_and_grant_ai_access(referrer_id)
        except Exception as e:
            logging.error(f"Backfill AI-access xatosi ({referrer_id}): {e}")


async def on_startup(_):
    asyncio.create_task(hourly_scheduler())
    asyncio.create_task(backfill_ai_access())


# ---------------------------
# RUN
# ---------------------------

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
