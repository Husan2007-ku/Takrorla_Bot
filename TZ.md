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
