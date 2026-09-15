
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
