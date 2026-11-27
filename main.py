import asyncio
import logging
import os
import html
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
# ================= НАСТРОЙКИ =================
BOT_TOKEN = '8415272108:AAH2367P_wG12p1z6Y_7uK2d8x7W1rC7n0M'
PROVIDER_TOKEN = '390540012:LIVE:106036'
ADMIN_ID = 8263206496
PRICE_HINT = 8900       # 89 ₽ — Подсказка об отправителе
PRICE_REVEAL = 9900     # 99 ₽ — Полное раскрытие отправителя
PRICE_PROTECT = 11900   # 119 ₽ — Защита сообщения
PRICE_CRUSH = 8900      # 89 ₽ — Проверка взаимной симпатии
# =============================================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
# --- БАЗА ДАННЫХ ---
os.makedirs('/app/data', exist_ok=True)
conn = sqlite3.connect('/app/data/bot_database_v2.db')
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS messages (
id INTEGER PRIMARY KEY AUTOINCREMENT,
sender_id INTEGER,
sender_username TEXT,
sender_name TEXT,
sender_lang TEXT,
target_id INTEGER,
is_protected INTEGER DEFAULT 0
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
user_id INTEGER PRIMARY KEY,
username TEXT,
joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS payments (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
amount INTEGER,
payment_type TEXT,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS crushes (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
user_username TEXT,
target_username TEXT,
UNIQUE(user_id, target_username)
)
''')
conn.commit()
Временные хранилища состояний
user_states = {}       # {user_id: {'mode': 'send'/'reply'/'crush', 'target_id': int}}
pending_messages = {}  # {temp_id: {...}}
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def estimate_reg_year(user_id: int) -> str:
if user_id < 100000000:
return "2013–2015 гг. (очень старый аккаунт)"
elif user_id < 500000000:
return "2016–2018 гг."
elif user_id < 1200000000:
return "2019–2020 гг."
elif user_id < 5000000000:
return "2021–2022 гг."
elif user_id < 7000000000:
return "2023–2024 гг."
else:
return "2025–2026 гг. (свежий аккаунт)"
def save_message_to_db(sender: types.User, target_id: int, is_protected: int = 0) -> int:
username = f"@{sender.username}" if sender.username else "скрыт"
full_name = sender.full_name or "Без имени"
lang = sender.language_code or "неизвестен"
cursor.execute(
'INSERT INTO messages (sender_id, sender_username, sender_name, sender_lang, target_id, is_protected) VALUES (?, ?, ?, ?, ?, ?)',
(sender.id, username, full_name, lang, target_id, is_protected)
)
conn.commit()
return cursor.lastrowid
async def send_personal_link(user_id: int):
bot_info = await bot.get_me()
link = f"https://t.me{bot_info.username}?start={user_id}"
kb = InlineKeyboardMarkup(inline_keyboard=[
[InlineKeyboardButton(text="💘 Кто в тебя влюблён (Crush-тест)", callback_data="menu_crush")],
[InlineKeyboardButton(text="📜 Правила сервиса", callback_data="menu_terms")]
])
await bot.send_message(
user_id,
f"🔗 Твоя личная ссылка для анонимных сообщений:\n\n"
f"{link}\n\n"
f"Размести её в профиле, сторис или отправь друзьям, чтобы получать тайные признания!",
parse_mode="Markdown",
reply_markup=kb
)
async def deliver_to_target(msg_db_id: int, from_chat_id: int, from_message_id: int, target_id: int, is_reply: bool = False):
kb = InlineKeyboardMarkup(inline_keyboard=[
[InlineKeyboardButton(text="↩️ Ответить анонимно", callback_data=f"reply_{msg_db_id}")],
[
InlineKeyboardButton(text="🔍 Подсказка (89 ₽)", callback_data=f"hint_{msg_db_id}"),
InlineKeyboardButton(text="🕵️‍♂️ Узнать точно (99 ₽)", callback_data=f"reveal_{msg_db_id}")
]
])
header = "💌 Тебе пришёл анонимный ответ!" if is_reply else "💌 Тебе пришло новое анонимное сообщение!"
try:
await bot.send_message(target_id, header, parse_mode="Markdown")
await bot.copy_message(
chat_id=target_id,
from_chat_id=from_chat_id,
message_id=from_message_id,
reply_markup=kb
)
return True
except Exception:
return False
# --- КОМАНДЫ ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
uname = message.from_user.username.lower() if message.from_user.username else None
cursor.execute('INSERT OR REPLACE INTO users (user_id, username) VALUES (?, ?)', (message.from_user.id, uname))
conn.commit()
args = command.args
if args and args.isdigit():
    target_id = int(args)
    if target_id == message.from_user.id:
        await message.answer("😅 Нельзя отправить анонимное сообщение самому себе!\nВот твоя ссылка для друзей:")
        await send_personal_link(message.from_user.id)
        return

    user_states[message.from_user.id] = {'mode': 'send', 'target_id': target_id, 'is_reply': False}
    await message.answer(
        "✍️ **Напиши сообщение для этого пользователя!**\n\n"
        "Можно отправить текст, фото, видео, голосовое или стикер. Оно будет доставлено полностью анонимно.",
        parse_mode="Markdown"
    )
else:
    user_states.pop(message.from_user.id, None)
    await send_personal_link(message.from_user.id)
@dp.message(Command('link'))
async def cmd_link(message: types.Message):
await send_personal_link(message.from_user.id)
@dp.message(Command('terms'))
async def cmd_terms(message: types.Message):
await message.answer(
"📜 Правила и условия сервиса\n\n"
"• Отправка и ответ на анонимные сообщения бесплатны.\n"
"• Подсказка (89 ₽): предоставляет косвенные данные об отправителе (первая буква имени, наличие @username, язык и примерный год регистрации).\n"
"• Раскрытие автора (99 ₽): показывает @username или ссылку на профиль отправителя, если им не была активирована защита.\n"
"• Защита сообщения (119 ₽): гарантирует конфиденциальность отправителя. При попытке раскрытия защищённого сообщения получателю выдаётся официальный статус конфиденциальности (услуга проверки считается оказанной).\n\n"
"Поддержка: @ramych1",
parse_mode="Markdown"
)
@dp.callback_query(F.data == "menu_terms")
async def cb_terms(callback: types.CallbackQuery):
await callback.answer()
await cmd_terms(callback.message)
# --- CRUSH-ТЕСТ (ВЗАИМНАЯ СИМПАТИЯ) ---
@dp.callback_query(F.data == "menu_crush")
async def cb_crush(callback: types.CallbackQuery):
await callback.answer()
if not callback.from_user.username:
await callback.message.answer(
"❗ Чтобы участвовать в проверке взаимной симпатии, установи публичный @username в настройках своего профиля Telegram, а затем нажми кнопку снова!",
parse_mode="Markdown"
)
return
user_states[callback.from_user.id] = {'mode': 'crush'}
await callback.message.answer(
    "💘 **Проверка взаимной симпатии**\n\n"
    "Отправь `@username` человека, который тебе нравится (например: `@durov`).\n"
    "Если он тоже укажет тебя — мы сразу сообщим о совпадении!",
    parse_mode="Markdown"
)
# --- ОБРАБОТКА ВХОДЯЩИХ СООБЩЕНИЙ ---
@dp.message(~F.successful_payment, ~F.text.startswith('/'))
async def handle_messages(message: types.Message):
user_id = message.from_user.id
state = user_states.get(user_id)
if not state:
    await message.answer(
        "🤔 Чтобы отправить анонимное сообщение, нужно перейти по личной ссылке человека.\n\n"
        "А если хочешь получить свою ссылку для друзей — нажми /link"
    )
    return

# Ветка Crush-теста
if state.get('mode') == 'crush':
    if not message.text or not message.text.strip().startswith('@'):
        await message.answer("✍️ Введи корректный @username, начиная со знака @ (например: `@ivanov`).", parse_mode="Markdown")
        return

    target_uname = message.text.strip().lstrip('@').lower()
    my_uname = (message.from_user.username or "").lower()

    if not my_uname:
        await message.answer("❗ Установи @username в настройках Telegram, чтобы пользоваться этой функцией.")
        user_states.pop(user_id, None)
        return

    if target_uname == my_uname:
        await message.answer("😅 Укажи @username другого человека, а не свой собственный!", parse_mode="Markdown")
        return

    cursor.
execute('INSERT OR IGNORE INTO crushes (user_id, user_username, target_username) VALUES (?, ?, ?)', (user_id, my_uname, target_uname))
    conn.commit()
    user_states.pop(user_id, None)

    # Проверяем, указал ли этот человек нас ранее
    cursor.execute('SELECT user_id FROM crushes WHERE user_username = ? AND target_username = ?', (target_uname, my_uname))
    match = cursor.fetchone()

    if match:
        other_user_id = match[0]
        kb_me = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💘 Узнать кто совпал (89 ₽)", callback_data=f"paycrush_{other_user_id}")]
        ])
        kb_other = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💘 Узнать кто совпал (89 ₽)", callback_data=f"paycrush_{user_id}")]
        ])
await message.answer("🔥 У ТЕБЯ ВЗАИМНАЯ СИМПАТИЯ!\nОдин из указанных тобой людей тоже отметил тебя!", parse_mode="Markdown", reply_markup=kb_me)
try:
await bot.send_message(other_user_id, "🔥 У ТЕБЯ ВЗАИМНАЯ СИМПАТИЯ!\nЧеловек из твоего списка симпатий только что отметил тебя!", parse_mode="Markdown", reply_markup=kb_other)
except Exception:
pass
else:
await message.answer(
f"✅ Готово! Мы тайно сохранили @{target_uname} в твой список симпатий.\n"
f"Как только этот человек тоже укажет твой @{my_uname}, ты сразу получишь уведомление!",
parse_mode="Markdown"
)
return
# Ветка отправки анонимного сообщения или ответа
target_id = state['target_id']
is_reply = state.get('is_reply', False)
temp_id = f"{user_id}_{message.message_id}"
pending_messages[temp_id] = {
'sender': message.from_user,
'chat_id': message.chat.id,
'message_id': message.message_id,
'target_id': target_id,
'is_reply': is_reply
}
user_states.pop(user_id, None)
kb = InlineKeyboardMarkup(inline_keyboard=[
[InlineKeyboardButton(text="🛡 Да, защитить (119 ₽)", callback_data=f"protect_{temp_id}")],
[InlineKeyboardButton(text="🕊 Нет, отправить бесплатно", callback_data=f"free_{temp_id}")]
])
await message.answer(
"🛡 Хотите защитить сообщение от раскрытия?\n\n"
"Получатель может оплатить функцию раскрытия отправителя и узнать, кто вы. "
"Защита за 119 ₽ гарантирует, что ваше имя и @username останутся в тайне.",
parse_mode="Markdown",
reply_markup=kb
)

# --- АНОНИМНЫЙ ОТВЕТ ---
@dp.callback_query(F.data.startswith('reply_'))
async def process_reply_btn(callback: types.CallbackQuery):
await callback.answer()
msg_db_id = int(callback.data.split('_')[1])
cursor.execute('SELECT sender_id FROM messages WHERE id = ?', (msg_db_id,))
row = cursor.fetchone()
if not row:
await callback.message.answer("❌ Не удалось найти ветку диалога.")
return
sender_id = row[0]
user_states[callback.from_user.id] = {'mode': 'reply', 'target_id': sender_id, 'is_reply': True}
await callback.message.answer(
"↩️ Напиши свой анонимный ответ!\nОн придёт автору этого сообщения, при этом твоя личность останется скрытой.",
parse_mode="Markdown"
)

# --- БЕСПЛАТНАЯ ОТПРАВКА ---
@dp.callback_query(F.data.startswith('free_'))
async def process_free_send(callback: types.CallbackQuery):
temp_id = callback.data.replace('free_', '')
data = pending_messages.pop(temp_id, None)
if not data:
await callback.answer("Это сообщение уже обработано.", show_alert=True)
return
msg_db_id = save_message_to_db(data['sender'], data['target_id'], is_protected=0)
success = await deliver_to_target(msg_db_id, data['chat_id'], data['message_id'], data['target_id'], data.get('is_reply', False))
if success:
await callback.message.edit_text(
"✅ Сообщение успешно доставлено анонимно!\n\n"
"Хочешь тоже получать анонимные сообщения? Нажми /link",
parse_mode="Markdown"
)
else:
await callback.message.edit_text("❌ Не удалось доставить сообщение (возможно, пользователь заблокировал бота).")

# --- ПЛАТНАЯ ЗАЩИТА (119 РУБ.) ---
@dp.callback_query(F.data.startswith('protect_'))
async def process_protect_send(callback: types.CallbackQuery):
await callback.answer()
temp_id = callback.data.replace('protect_', '')
if temp_id not in pending_messages:
await callback.message.edit_text("Время ожидания истекло или сообщение уже отправлено.")
return
await bot.send_invoice(
chat_id=callback.from_user.id,
title="🛡 Защита анонимности",
description="Гарантированное скрытие вашего профиля от функции раскрытия отправителя.",
payload=f"payprotect_{temp_id}",
provider_token=PROVIDER_TOKEN,
currency="RUB",
prices=[LabeledPrice(label="Защита сообщения", amount=PRICE_PROTECT)]
)

# --- ЭКСТРЕННАЯ ДОПЛАТА ЗАЩИТЫ ПО ПУШУ (119 РУБ.) ---
@dp.callback_query(F.data.startswith('urgentprotect_'))
async def process_urgent_protect(callback: types.CallbackQuery):
await callback.answer()
msg_db_id = int(callback.data.split('')[1])
await bot.send_invoice(
chat_id=callback.from_user.id,
title="🛡 Экстренная защита сообщения",
description="Срочно заблокировать раскрытие вашего профиля для получателя!",
payload=f"payurgent{msg_db_id}",
provider_token=PROVIDER_TOKEN,
currency="RUB",
prices=[LabeledPrice(label="Экстренная защита", amount=PRICE_PROTECT)]
)

# --- ПОДСКАЗКА ОБ ОТПРАВИТЕЛЕ (89 РУБ.) ---
@dp.callback_query(F.data.startswith('hint_'))
async def process_hint_request(callback: types.CallbackQuery):
await callback.answer()
msg_db_id = int(callback.data.split('_')[1])
# Триггер-пуш отправителю
cursor.execute('SELECT sender_id, is_protected FROM messages WHERE id = ?', (msg_db_id,))
row = cursor.fetchone()
if row and row[1] == 0 and row[0] != callback.from_user.id:
kb_push = InlineKeyboardMarkup(inline_keyboard=[
[InlineKeyboardButton(text="🛡 Экстренно защитить (119 ₽)", callback_data=f"urgentprotect_{msg_db_id}")]
])
try:
await bot.send_message(
row[0],
"⚠️ Внимание! Получатель прямо сейчас запросил подсказку о том, кто написал ему сообщение!\n"
"Успей поставить защиту, пока он не оплатил раскрытие 👇",
parse_mode="Markdown",
reply_markup=kb_push
)
except Exception:
pass
await bot.send_invoice(
chat_id=callback.from_user.id,
title="🔍 Подсказка об отправителе",
description="Узнай первую букву имени, наличие @username, язык и год создания аккаунта.",
payload=f"payhint_{msg_db_id}",
provider_token=PROVIDER_TOKEN,
currency="RUB",
prices=[LabeledPrice(label="Подсказка об авторе", amount=PRICE_HINT)]
)

# --- ПОЛНОЕ РАСКРЫТИЕ ОТПРАВИТЕЛЯ (99 РУБ.) ---
@dp.callback_query(F.data.startswith('reveal_'))
async def process_reveal_request(callback: types.CallbackQuery):
await callback.answer()
msg_db_id = int(callback.data.split('_')[1])
# Триггер-пуш отправителю
cursor.execute('SELECT sender_id, is_protected FROM messages WHERE id = ?', (msg_db_id,))
row = cursor.fetchone()
if row and row[1] == 0 and row[0] != callback.from_user.id:
kb_push = InlineKeyboardMarkup(inline_keyboard=[
[InlineKeyboardButton(text="🛡 Экстренно защитить (119 ₽)", callback_data=f"urgentprotect_{msg_db_id}")]
])
try:
await bot.send_message(
row[0],
"🚨 Тревога! Получатель прямо сейчас нажал «Узнать кто написал»!\n"
"Успей включить защиту за 119 ₽, чтобы твой @username не раскрылся 👇",
parse_mode="Markdown",
reply_markup=kb_push
)
except Exception:
pass
await bot.send_invoice(
chat_id=callback.from_user.id,
title="🕵️‍♂️ Раскрытие отправителя",
description="Проверка статуса и получение @username автора сообщения.",
payload=f"payreveal_{msg_db_id}",
provider_token=PROVIDER_TOKEN,
currency="RUB",
prices=[LabeledPrice(label="Раскрытие отправителя", amount=PRICE_REVEAL)]
)

# --- ОПЛАТА CRUSH-ТЕСТА (89 РУБ.) ---
@dp.callback_query(F.data.startswith('paycrush_'))
async def process_crush_pay(callback: types.CallbackQuery):
await callback.answer()
match_user_id = int(callback.data.split('')[1])
await bot.send_invoice(
chat_id=callback.from_user.id,
title="💘 Взаимная симпатия",
description="Узнать, с кем у вас совпала симпатия!",
payload=f"crushdone{match_user_id}",
provider_token=PROVIDER_TOKEN,
currency="RUB",
prices=[LabeledPrice(label="Открыть взаимную симпатию", amount=PRICE_CRUSH)]
)

# --- ПОДТВЕРЖДЕНИЕ ПЕРЕД ОПЛАТОЙ ---
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

# --- УСПЕШНАЯ ОПЛАТА ---
@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
try:
payload = message.successful_payment.invoice_payload
# 1. Оплачена защита при отправке (119 ₽)
if payload.startswith('payprotect_'):
temp_id = payload.replace('payprotect_', '')
data = pending_messages.pop(temp_id, None)
if not data:
await message.answer('✅ Оплата прошла, но сообщение уже было отправлено ранее.')
return
msg_db_id = save_message_to_db(data['sender'], data['target_id'], is_protected=1)
success = await deliver_to_target(msg_db_id, data['chat_id'], data['message_id'], data['target_id'], data.get('is_reply', False))
if success:
cursor.execute('INSERT INTO payments (user_id, amount, payment_type) VALUES (?, ?, ?)', (message.from_user.id, 119, 'protect'))
conn.commit()
await message.answer(
'✅ Оплата прошла успешно!\n\n'
'Ваше сообщение отправлено под полной защитой. Получатель не сможет узнать, кто вы.\n\n'
'Получить свою ссылку: /link',
parse_mode='Markdown'
)
else:
await message.answer('❌ Не удалось доставить сообщение (получатель заблокировал бота).')
# 2. Оплачена экстренная защита по пушу (119 ₽)
elif payload.startswith('payurgent_'):
msg_db_id = int(payload.replace('payurgent_', ''))
cursor.execute('UPDATE messages SET is_protected = 1 WHERE id = ?', (msg_db_id,))
cursor.execute('INSERT INTO payments (user_id, amount, payment_type) VALUES (?, ?, ?)', (message.from_user.id, 119, 'urgent_protect'))
conn.commit()
await message.answer('🛡 Защита активирована! Теперь даже если получатель оплатит раскрытие, он увидит только статус конфиденциальности.', parse_mode='Markdown')
# 3. Оплачена подсказка (89 ₽)
elif payload.startswith('payhint_'):
msg_db_id = int(payload.replace('payhint_', ''))
cursor.execute('SELECT sender_id, sender_username, sender_name, sender_lang, is_protected FROM messages WHERE id = ?', (msg_db_id,))
row = cursor.fetchone()
if not row:
await message.answer('❌ Информация об этом сообщении не найдена.')
return
cursor.execute('INSERT INTO payments (user_id, amount, payment_type) VALUES (?, ?, ?)', (message.from_user.id, 89, 'hint'))
conn.commit()
sender_id, sender_username, sender_name, sender_lang, is_protected = row
if is_protected == 1:
await message.answer('😶‍🌫️ Извините, сообщение оплачено и считается конфиденциальным 😶‍🌫️')
else:
first_letter = sender_name[0].upper() if sender_name else "?"
has_uname = "есть (@username установлен)" if sender_username != "скрыт" else "скрыт"
reg_year = estimate_reg_year(sender_id)
await message.answer(
f"🔍 Подсказка об отправителе:\n\n"
f"• Первая буква имени: {first_letter}\n"
f"• Публичный юзернейм: {has_uname}\n"
f"• Язык приложения: {sender_lang}\n"
f"• Регистрация аккаунта: {reg_year}",
parse_mode='Markdown'
)
# 4. Оплачено полное раскрытие отправителя (99 ₽)
elif payload.startswith('payreveal_'):
msg_db_id = int(payload.replace('payreveal_', ''))
cursor.execute('SELECT sender_id, sender_username, sender_name, is_protected FROM messages WHERE id = ?', (msg_db_id,))
row = cursor.fetchone()
if not row:
await message.answer('❌ Информация об этом сообщении не найдена.')
return
cursor.execute('INSERT INTO payments (user_id, amount, payment_type) VALUES (?, ?, ?)', (message.from_user.id, 99, 'reveal'))
conn.commit()
sender_id, sender_username, sender_name, is_protected = row
if is_protected == 1:
await message.answer('😶‍🌫️ Извините, сообщение оплачено и считается конфиденциальным 😶‍🌫️')
else:
if sender_username != "скрыт":
await message.answer(f'🕵️‍♂️ Это сообщение отправил(а): {sender_username}')
else:
safe_name = html.escape(sender_name)
await message.answer(
f'🕵️‍♂️ У отправителя скрыт @username, но вот его профиль:\n'
f'Имя: {safe_name}',
parse_mode='HTML'
)
# 5. Оплачен Crush-тест (89 ₽)
elif payload.startswith('crushdone_'):
match_user_id = int(payload.replace('crushdone_', ''))
cursor.execute('SELECT username FROM users WHERE user_id = ?', (match_user_id,))
row = cursor.fetchone()
cursor.execute('INSERT INTO payments (user_id, amount, payment_type) VALUES (?, ?, ?)', (message.from_user.id, 89, 'crush'))
conn.commit()
if row and row[0]:
await message.answer(f"💘 Твоя взаимная симпатия: @{row[0]}\nМожешь смело написать первым!", parse_mode='Markdown')
else:
await message.answer(f'💘 Открыть профиль взаимной симпатии', parse_mode='HTML')
except Exception as e:
await message.answer(f'⚠️ Техническая ошибка при выдаче: {e}')

# --- АДМИН-ПАНЕЛЬ (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА) ---
@dp.message(Command('admin'))
async def cmd_admin(message: types.Message):
if message.from_user.id != ADMIN_ID:
return
cursor.execute('SELECT COUNT(*) FROM users')
total_users = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(*) FROM messages')
total_messages = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM payments')
total_payments_count, total_revenue = cursor.fetchone()
cursor.execute("SELECT COUNT(*) FROM payments WHERE payment_type IN ('protect', 'urgent_protect')")
protect_count = cursor.fetchone()[0]
cursor.
execute("SELECT COUNT(*) FROM payments WHERE payment_type = 'reveal'")
reveal_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM payments WHERE payment_type = 'hint'")
hint_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM payments WHERE payment_type = 'crush'")
crush_count = cursor.fetchone()[0]
text = (
"📊 Админ-статистика бота\n\n"
f"👥 Всего пользователей: {total_users}\n"
f"✉️ Отправлено сообщений: {total_messages}\n\n"
f"💳 Успешных оплат: {total_payments_count} (на сумму {total_revenue} ₽)\n"
f" • Защита / экстренная (119 ₽): {protect_count}\n"
f" • Полное раскрытие (99 ₽): {reveal_count}\n"
f" • Подсказки (89 ₽): {hint_count}\n"
f" • Crush-тесты (89 ₽): {crush_count}"
)
await message.answer(text, parse_mode='Markdown')
async def main():
await bot.delete_webhook(drop_pending_updates=True)
await dp.start_polling(bot)
if name == 'main':
asyncio.run(main())
