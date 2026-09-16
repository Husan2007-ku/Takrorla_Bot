# Takrorla_Bot — TZ (v2)

## 1. Muammo va maqsad

Bot allaqachon ishlab chiqilgan — spaced repetition asosida Telegramga yozilgan ma'lumotni 1/7/30 kunda eslatish. Hozir ikkita muammo bor:

- **Ishlamayapti**: `bot.py` ichida token hardcoded, repo public GitHub'da — Telegram tokenni revoke qilgan (`401 Unauthorized`). Bot hech kimga javob bermaydi.
- **Faqat asosiy funksiya bor**: statistika, karta boshqaruvi, moslashuvchan takrorlash yo'q.

Maqsad — botni qayta ishga tushirish + kengaytirish. Bu safar **boshqa hech bir loyihaga (Develop UZ, MEROS AI va h.k.) texnik integratsiya qilinmaydi** — Takrorla_Bot mustaqil mahsulot bo'lib qoladi va Husanning barcha loyihalari/kanallari uchun **obunachi bazasi + reklama kanali** vazifasini bajaradi.

## 2. Foydalanuvchi

Har qanday narsani (til, IELTS so'z, konspekt, foydali fikr, din, umumiy bilim) yodda saqlashni istagan har qanday o'rganuvchi — faqat ingliz tili o'rganuvchilari bilan cheklanmaydi.

## 3. Asosiy funksiyalar

### MVP (hoziroq)

**P0 — xavfsizlik**
- Token `.env` fayldan (`python-dotenv`), koddan hardcode olib tashlanadi
- `.gitignore` ga `.env` va `*.db` qo'shiladi

**Kuchaytirilgan takrorlash (SM-2 soddalashtirilgan)**
- Qattiq 1/7/30 kun o'rniga: har karta uchun `ease_factor` + `interval_days`
- 4 daraja: Again / Hard / Good / Easy (Anki uslubi)

**Karta boshqaruvi**
- `/list` — barcha kartalar, har biriga o'chirish tugmasi
- `/stats` — jami karta, bugun takrorlangan, streak

**Sozlanuvchi eslatma vaqti**
- `/vaqt HH` — foydalanuvchi eslatma soatini tanlaydi (default 9:00, Asia/Tashkent)
- Scheduler har soatda tekshiradi, faqat shu soatga to'g'ri kelgan foydalanuvchilarga yuboradi (oldingi versiyada `sleep(86400)` drift qilardi)

**O'sish / reklama tizimi (Husanning asosiy g'oyasi)**
- `/start` xabarida HusanAI kanal + Aql Klubi + boshqa loyihalar linki
- Har foydalanuvchi `users` jadvalida saqlanadi (obunachi bazasi)
- Promo-rotatsiya: takrorlash xabarlaridan so'ng vaqti-vaqti bilan (masalan har 5-seansda 1 marta) `promo_messages` jadvalidan tasodifiy reklama matni qo'shiladi
- `/broadcast <matn>` — faqat admin (Husan) uchun, barcha foydalanuvchilarga bir martalik e'lon (yangi loyiha chiqqanda ishlatiladi)
- `/invite` — referral link (`t.me/<bot>?start=ref_<user_id>`), kim kimni taklif qilganini kuzatish
- `/admin_stats` — jami foydalanuvchilar soni, kunlik yangi qo'shilganlar (faqat admin)

### Keyingi bosqichlar (v2+, hoziroq yozilmaydi)

- Kategoriya/deck tizimi
- Active recall test rejimi (javobni yashirib, keyin ochish)
- Rasm/audio karta
- Guruh/repetitsiya sharing (o'qituvchi rejimi)

## 4. Texnik stack

- Python, `aiogram` 2.x (mavjud kodga mos, qayta yozilmaydi)
- SQLite (`data.db`) — hozirgi bosqichda yetarli
- `python-dotenv` — token va admin ID uchun
- `zoneinfo` (Asia/Tashkent) — eslatma vaqti uchun
- Deploy: Render Background Worker (`Procfile` allaqachon tayyor: `worker: python bot.py`)

## 5. Ma'lumotlar modeli

```
users
- user_id (PK)
- username
- first_name
- joined_at
- reminder_hour (default 9)
- referred_by (nullable, user_id)

cards
- id (PK)
- user_id
- content
- ease_factor (default 2.5)
- interval_days (default 1)
- reps (default 0)
- due_date
- created_at

promo_messages
- id (PK)
- text
- active (default 1)
```

## 6. Muvaffaqiyat mezoni

- Bot qayta ishga tushadi, xavfsizlik xatosi yo'q (token public repo'da ko'rinmaydi)
- Kamida bitta promo-rotatsiya orqali boshqa kanal/loyihaga trafik yo'naladi
- `/broadcast` orqali yangi loyiha e'loni butun obunachi bazasiga bir marta yuboriladi
- Eslatmalar aniq belgilangan soatda keladi (drift yo'q)

## 7. v3 qo'shimchalari (2026-09-15)

**Tahrirlash (bug fix)**
- Muammo: kartani "tahrirlash" imkoniyati yo'q edi — foydalanuvchi xato matnni tuzatish uchun qayta yozganda, bu YANGI karta sifatida saqlanardi, eski (xato) karta bazada qolib, alohida o'z jadvali bo'yicha eslatilishda davom etardi.
- Yechim: har kartada ✏️ Tahrirlash tugmasi — bosilganda keyingi yuborilgan matn O'SHA kartani yangilaydi (id, due_date, ease_factor saqlanib qoladi), eski matn butunlay almashadi.

**Kategoriya (🗣 Til so'zi / 📚 Umumiy) va talaffuz (🔊)**
- Bot hamma uchun (tarix, matematika va h.k.) qolmoqda — SHUNING UCHUN talaffuz funksiyasi faqat foydalanuvchi kartani "🗣 Til so'zi" deb belgilaganda ko'rinadi.
- Yangi karta qo'shilganda va /list'da har karta ostida toggle tugma chiqadi — istalgan vaqt kategoriya o'zgartirilishi mumkin (eski kartalar ham).
- Talaffuz `gTTS` (Google TTS, bepul, API key kerak emas) orqali, kartaning " - " dan oldingi qismini (yoki butun matnni) ingliz tilida audio qilib yuboradi.
- `cards` jadvaliga `category TEXT DEFAULT 'other'` ustuni qo'shildi — mavjud bazalarga `ALTER TABLE` orqali, hech qanday qator o'chirilmaydi/yo'qolmaydi.

**Keyingi qadam (hoziroq yozilmagan)**: talaffuz tili hozircha faqat inglizcha (`lang="en"`) — boshqa tilni o'rganuvchilar uchun /list orqali til tanlash keyingi bosqichda qo'shilishi mumkin.

## 8. v4 qo'shimchasi (2026-09-16) — Referral orqali AI Test

**G'oya:** oddiy foydalanuvchi uchun bot avvalgidek (bepul, cheksiz) ishlayveradi — hech narsa qisqartirilmaydi. Qo'shimcha rag'bat sifatida: har **3 ta FAOL taklif** (ya'ni taklif qilingan kishi `/start` bosib, kamida 1 ta ma'lumot qo'shgan bo'lishi kerak — shunchaki start bosish yetarli emas) uchun taklif qilgan odamga **30 kunlik AI Test** funksiyasi ochiladi. Keyingi har 3 ta faol taklif yana 30 kun qo'shadi (mavjud muddat ustiga).

**AI Test nima qiladi:** foydalanuvchining saqlangan kartalaridan tasodifiy 5 tasi bo'yicha AI (Groq yoki Gemini, ikkalasi ham bepul tier bilan yetarli) qisqa savol tuzadi, foydalanuvchi o'z so'zi bilan javob yozadi, AI asl ma'lumot bilan solishtirib to'g'ri/noto'g'ri deb baholaydi va qisqa izoh beradi — bu "active recall" (TZ v2'da "keyingi bosqich" deb belgilangan funksiya) ning AI-kuchaytirilgan varianti.

**Texnik:**
- `users` jadvaliga `ai_access_until`, `ai_milestones_granted` ustunlari qo'shildi (`ALTER TABLE`, mavjud ma'lumot o'chmaydi).
- AI provider `AI_PROVIDER` (`groq`/`gemini`) va `AI_API_KEY` orqali `.env`'da sozlanadi — kod ikkalasini ham qo'llab-quvvatlaydi, provider almashtirish uchun kod o'zgarmaydi.
- `/invite` endi foydalanuvchiga necha faol taklifi borligini va keyingi ochilishgacha nechta qolganini ko'rsatadi.
- Deploy vaqtida (`on_startup`) bir martalik backfill ishga tushadi — bu funksiya chiqishidan OLDIN allaqachon 3+ faol taklifga ega bo'lganlarga ham adolat yuzasidan darhol ochib beriladi.
