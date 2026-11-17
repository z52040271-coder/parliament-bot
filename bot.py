import asyncio
import logging
import os
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Токен вашего бота «Парламент»
TOKEN = "9101171778:AAFtjTjbUfN28qBvByaQaTJnCxdKL41M4KY"
ADMIN_IDS = [7172212108]  # Ваш реальный Telegram ID администратора

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def handle(request):
    return web.Response(text="Parliament Bot is alive!")

app = web.Application()
app.router.add_get("/", handle)

async def start_web_server():
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("parliament.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            points INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0,
            msg_count INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time_str TEXT,
            active INTEGER DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meeting_responses (
            user_id INTEGER,
            meeting_id INTEGER,
            status TEXT,
            PRIMARY KEY (user_id, meeting_id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_db():
    return sqlite3.connect("parliament.db")

# --- 1. СИСТЕМА БАЛЛОВ И УЧЕТ СООБЩЕНИЙ ---
@dp.message(F.text & ~F.text.startswith("/"))
async def message_handler(message: types.Message):
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    text = message.text

    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT points, warnings, msg_count FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users (user_id, username, points, warnings, msg_count) VALUES (?, ?, 0, 0, 0)", (user_id, username))
        conn.commit()
        points, warnings, msg_count = 0, 0, 0
    else:
        points, warnings, msg_count = row

    if text.strip().lower().startswith("#идея"):
        points += 5
        await message.reply("💡 Зафиксирована идея! Вам начислено +5 баллов.")

    msg_count += 1
    if msg_count >= 100:
        points += 1
        msg_count = 0
        await message.answer(f"🎉 @{username} получает +1 балл за каждые 100 сообщений!")

    if message.reply_to_message and message.reply_to_message.text:
        if message.reply_to_message.text.lower().startswith("#идея"):
            words_count = len(text.split())
            if words_count > 10:
                points += 1
                await message.reply("💬 За содержательный отзыв на идею (+1 балл).")

    cursor.execute("UPDATE users SET points = ?, msg_count = ? WHERE user_id = ?", (points, msg_count, user_id))
    conn.commit()
    
    if message.reply_to_message:
        text_lower = text.strip().lower()
        if text_lower.startswith("прийду") or text_lower.startswith("приду"):
            cursor.execute("SELECT id FROM meetings WHERE active = 1 ORDER BY id DESC LIMIT 1")
            m = cursor.fetchone()
            if m:
                meeting_id = m[0]
                cursor.execute("REPLACE INTO meeting_responses (user_id, meeting_id, status) VALUES (?, ?, ?)", 
                               (user_id, meeting_id, 'coming'))
                conn.commit()
                await message.reply("✅ Записано: вы прийдете на собрание.")
        elif text_lower.startswith("не прийду") or text_lower.startswith("не приду"):
            cursor.execute("SELECT id FROM meetings WHERE active = 1 ORDER BY id DESC LIMIT 1")
            m = cursor.fetchone()
            if m:
                meeting_id = m[0]
                cursor.execute("REPLACE INTO meeting_responses (user_id, meeting_id, status) VALUES (?, ?, ?)", 
                               (user_id, meeting_id, 'absent_excused'))
                conn.commit()
                await message.reply("📝 Записано: отсутствие по уважительной причине.")
                
    conn.close()

# --- 2. СОБРАНИЯ И ОТЧЕТНОСТЬ ---
@dp.message(Command("собрание"))
async def cmd_meeting(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("❌ Команда только для админов.")
    
    args = message.text.split(maxsplit=1)
    time_val = args[1] if len(args) > 1 else "скоро"
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO meetings (time_str, active) VALUES (?, 1)", (time_val,))
    conn.commit()
    conn.close()

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Приду", callback_data="meet_yes")
    builder.button(text="❌ Не приду (уважительная)", callback_data="meet_no")
    builder.adjust(2)

    await message.answer(
        f"🚨 **Внимание! Запланирован общий сбор в {time_val}**\n"
        "Пожалуйста, скажите, удобно ли вам будет собраться? Нажмите кнопку ниже или ответьте на это сообщение словами «Приду» / «Не приду».",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("meet_"))
async def callback_meeting(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM meetings WHERE active = 1 ORDER BY id DESC LIMIT 1")
    m = cursor.fetchone()
    
    if not m:
        await callback.answer("Нет активных собраний.", show_alert=True)
        return
    
    meeting_id = m[0]
    if callback.data == "meet_yes":
        cursor.execute("REPLACE INTO meeting_responses (user_id, meeting_id, status) VALUES (?, ?, ?)", (user_id, meeting_id, 'coming'))
        status_text = "Вы записаны: прийдете."
    else:
        cursor.execute("REPLACE INTO meeting_responses (user_id, meeting_id, status) VALUES (?, ?, ?)", (user_id, meeting_id, 'absent_excused'))
        status_text = "Записано: отсутствие по уважительной причине."
        
    conn.commit()
    conn.close()
    await callback.answer(status_text, show_alert=True)
    await callback.message.edit_text(f"{callback.message.text}\n\n*Ваш ответ сохранен!*", parse_mode="Markdown")

@dp.message(Command("список_сбора"))
async def cmd_meeting_list(message: types.Message):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, time_str FROM meetings WHERE active = 1 ORDER BY id DESC LIMIT 1")
    m = cursor.fetchone()
    if not m:
        return await message.answer("Активных сборов сейчас нет.")
    
    meeting_id, time_str = m
    cursor.execute("SELECT username, status FROM meeting_responses JOIN users ON users.user_id = meeting_responses.user_id WHERE meeting_id = ?", (meeting_id,))
    rows = cursor.fetchall()
    conn.close()

    text = f"📊 **Отчет по собранию на {time_str}:**\n\n"
    for uname, st in rows:
        icon = "✅" if st == 'coming' else "⚠️"
        text += f"• @{uname}: {icon} {st}\n"
    
    await message.answer(text, parse_mode="Markdown")

# --- 3. СИСТЕМА ПРЕДУПРЕЖДЕНИЙ ---
@dp.message(Command("предупреждение"))
async def cmd_warn(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("❌ Только для админов.")
    
    if not message.reply_to_message:
        return await message.answer("⚠️ Эту команду нужно отправлять **в ответ** на сообщение нарушителя.")
    
    target_user = message.reply_to_message.from_user
    user_id = target_user.id
    username = target_user.username or target_user.first_name

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT warnings FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    warnings = (row[0] if row else 0) + 1
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, warnings) VALUES (?, ?, ?)", (user_id, username, warnings))
    cursor.execute("UPDATE users SET warnings = ? WHERE user_id = ?", (warnings, user_id))
    conn.commit()
    conn.close()

    await message.answer(f"⚠️ Участник @{username} получил предупреждение. Всего предупреждений: {warnings}/3.")

    if warnings >= 3:
        await message.answer(
            f"🚨 **ВНИМАНИЕ!** Участник @{username} накопил 3 предупреждения!\n"
            "Запущено официальное голосование за кик этого участника!", 
            parse_mode="Markdown"
        )

@dp.message(Command("снять_варн"))
async def cmd_unwarn(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("❌ Только для админов.")
    if not message.reply_to_message:
        return await message.answer("Ответьте на сообщение пользователя, которому хотите снять предупреждение.")
    
    target_user = message.reply_to_message.from_user
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT warnings FROM users WHERE user_id = ?", (target_user.id,))
    row = cursor.fetchone()
    if row and row[0] > 0:
        new_w = row[0] - 1
        cursor.execute("UPDATE users SET warnings = ? WHERE user_id = ?", (new_w, target_user.id))
        conn.commit()
        await message.answer(f"✅ Предупреждение снято с @{target_user.username or target_user.first_name}. Текущих варнов: {new_w}")
    else:
        await message.answer("У пользователя нет активных предупреждений.")
    conn.close()

@dp.message(Command("варны"))
async def cmd_list_warns(message: types.Message):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT username, warnings FROM users WHERE warnings > 0")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return await message.answer("🎉 Нарушителей и предупреждений нет!")

    text = "📋 **Статистика предупреждений:**\n\n"
    for uname, w in rows:
        text += f"• @{uname}: {w} предупреждений\n"
    await message.answer(text, parse_mode="Markdown")

# --- 4. ПРАВИЛА И УСТАВ ---
@dp.message(Command("правила"))
async def cmd_rules(message: types.Message):
    text = (
        "📜 **Правила по темам чата:**\n\n"
        "• **Чат для работы:** Писать строго по делу.\n"
        "• **Голосование:** Только голосовать, без текстовых сообщений.\n"
        "• **Свободное общение:** Без PDF-файлов и шок-контента.\n"
        "• **Чаты для идей:** В обоих писать только про идеи. В «неудачных» — если хотите возобновить обсуждение; в «удачных» — если про них забыли.\n"
        "• **Апелляции:** Писать строго по делу апелляций.\n"
        "• **Идеи в процессе:** Если считаете, что нужно ускорить её реализацию."
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("устав"))
async def cmd_ustav(message: types.Message):
    text = (
        "⚖️ **Устав Парламента:**\n\n"
        "1. Никакой коррупции и подобного — за нее кик из парламента.\n"
        "2. Идеи пишем только с хэштегом в начале (`#идея`).\n"
        "3. Сборы обязательны два раза в месяц.\n"
        "4. 3 предупреждения = запуск голосования за кик.\n"
        "5. Пропуск собрания без уважительной причины = 1 предупреждение.\n"
        "6. Нарушение правил чата = 1 предупреждение."
    )
    await message.answer(text, parse_mode="Markdown")

# Главная функция запуска
async def main():
    await start_web_server()
    print("Бот парламента запущен с веб-сервером!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
