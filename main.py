import os
import json
import time
import html
import asyncio
import logging
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
    WebAppInfo
)

logging.basicConfig(level=logging.INFO)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv('BOT_TOKEN')
PROVIDER_TOKEN = os.getenv('PROVIDER_TOKEN', '381764678:TEST:111111')
ADMIN_ID = int(os.getenv('ADMIN_ID', '8263206496'))
WEBAPP_URL = os.getenv('WEBAPP_URL', 'https://bot-1795118631-6172-ramych.bothost.tech')
PORT = int(os.getenv('PORT', 3000))

PRICE_HINT = 8900       # 89 руб.
PRICE_REVEAL = 9900     # 99 руб.
PRICE_PROTECT = 11900   # 119 руб.
PRICE_CRUSH = 8900      # 89 руб.

SUB_PLANS = {
    '1m': {'title': 'Подписка PRO на 1 месяц', 'days': 30, 'price': 19900},
    '6m': {'title': 'Подписка PRO на 6 месяцев', 'days': 180, 'price': 49900},
    '1y': {'title': 'Подписка PRO на 1 год', 'days': 365, 'price': 79900},
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= БАЗА ДАННЫХ =================
conn = sqlite3.connect('anon_bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    welcomed INTEGER DEFAULT 0,
    pro_until INTEGER DEFAULT 0,
    trial_used INTEGER DEFAULT 0,
    daily_chats INTEGER DEFAULT 0,
    window_start INTEGER DEFAULT 0
)
''')

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
CREATE TABLE IF NOT EXISTS crushes (
    user_id INTEGER PRIMARY KEY,
    crush_username TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    category TEXT,
    amount INTEGER,
    created_at INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS unlocked_messages (
    user_id INTEGER,
    msg_id INTEGER,
    unlock_type TEXT,
    PRIMARY KEY (user_id, msg_id, unlock_type)
)
''')
conn.commit()

# Безопасная миграция колонок для существующей базы
for col_sql in [
    'ALTER TABLE users ADD COLUMN welcomed INTEGER DEFAULT 0',
    'ALTER TABLE users ADD COLUMN pro_until INTEGER DEFAULT 0',
    'ALTER TABLE users ADD COLUMN trial_used INTEGER DEFAULT 0',
    'ALTER TABLE users ADD COLUMN daily_chats INTEGER DEFAULT 0',
    'ALTER TABLE users ADD COLUMN window_start INTEGER DEFAULT 0',
]:
    try:
        cursor.execute(col_sql)
        conn.commit()
    except sqlite3.OperationalError:
        pass

user_states = {}
urgent_pending = {}

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def register_user(user: types.User) -> bool:
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user.id,))
    exists = cursor.fetchone() is not None
    uname = user.username.lower() if user.username else None
    if not exists:
        cursor.execute(
            'INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?)',
            (user.id, uname, user.full_name or 'Пользователь')
        )
    else:
        cursor.execute(
            'UPDATE users SET username = ?, full_name = ? WHERE user_id = ?',
            (uname, user.full_name or 'Пользователь', user.id)
        )
    conn.commit()
    return not exists

def get_user_quota(user_id: int) -> dict:
    now = int(time.time())
    cursor.execute('SELECT pro_until, trial_used, daily_chats, window_start FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute('INSERT INTO users (user_id) VALUES (?)', (user_id,))
        conn.commit()
        return {'is_pro': False, 'trial_used': False, 'used': 0, 'reset_in': 0}

    pro_until, trial_used, daily_chats, window_start = row
    is_pro = (pro_until or 0) > now

    if window_start and (now - window_start) >= 86400:
        daily_chats = 0
        window_start = 0
        cursor.execute('UPDATE users SET daily_chats = 0, window_start = 0 WHERE user_id = ?', (user_id,))
        conn.commit()

    reset_in = max(0, 86400 - (now - window_start)) if window_start else 0
    return {
        'is_pro': is_pro,
        'trial_used': bool(trial_used),
        'used': daily_chats or 0,
        'reset_in': reset_in
    }

def consume_chat_quota(user_id: int) -> bool:
    q = get_user_quota(user_id)
    if q['is_pro']:
        return True
    if q['used'] >= 20:
        return False
    now = int(time.time())
    cursor.execute('SELECT daily_chats, window_start FROM users WHERE user_id = ?', (user_id,))
    used, w_start = cursor.fetchone()
    if not w_start or (now - w_start) >= 86400:
        w_start = now
        used = 1
    else:
        used = (used or 0) + 1
    cursor.execute('UPDATE users SET daily_chats = ?, window_start = ? WHERE user_id = ?', (used, w_start, user_id))
    conn.commit()
    return True

def is_dialog_protected(sender_id: int, target_id: int, msg_id: int = None) -> bool:
    if msg_id:
        cursor.execute('SELECT is_protected FROM messages WHERE id = ?', (msg_id,))
        r = cursor.fetchone()
        if r and r[0] == 1:
            return True
    cursor.execute(
        'SELECT 1 FROM messages WHERE sender_id = ? AND target_id = ? AND is_protected = 1 LIMIT 1',
        (sender_id, target_id)
    )
    return cursor.fetchone() is not None

def estimate_reg_year(user_id: int) -> str:
    if user_id < 100000000:
        return '2013–2015 гг.'
    if user_id < 500000000:
        return '2016–2018 гг.'
    if user_id < 1200000000:
        return '2019–2020 гг.'
    if user_id < 5000000000:
        return '2021–2022 гг.'
    if user_id < 7000000000:
        return '2023–2024 гг.'
    return '2025–2026 гг.'

def save_message_to_db(sender: types.User, target_id: int, is_protected: int = 0) -> int:
    username = f'@{sender.username}' if sender.username else 'скрыт'
    full_name = sender.full_name or 'Без имени'
    lang = sender.language_code or 'неизвестен'
    if is_protected == 0 and is_dialog_protected(sender.id, target_id):
        is_protected = 1
    cursor.execute(
        'INSERT INTO messages (sender_id, sender_username, sender_name, sender_lang, target_id, is_protected) VALUES (?, ?, ?, ?, ?, ?)',
        (sender.id, username, full_name, lang, target_id, is_protected)
    )
    conn.commit()
    return cursor.lastrowid

async def delayed_welcome_message(user_id: int):
    await asyncio.sleep(120)
    cursor.execute('SELECT welcomed FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if row and row[0] == 1:
        return
    cursor.execute('SELECT 1 FROM messages WHERE target_id = ? LIMIT 1', (user_id,))
    if cursor.fetchone():
        cursor.execute('UPDATE users SET welcomed = 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        return
    cursor.execute('UPDATE users SET welcomed = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    try:
        await bot.send_message(
            user_id,
            '💌 <b>Новое сообщение от тайного собеседника!</b>

'
            '👋 Привет! Я твой первый анонимный собеседник. Скорее выложи свою ссылку в сторис или отправь друзьям — уже через пару минут тебе начнут прилетать честные вопросы и признания!',
            parse_mode='HTML'
        )
    except Exception:
        pass

async def send_personal_link(message: types.Message, user_id: int):
    bot_info = await bot.get_me()
    link = f'https://t.me/{bot_info.username}?start={user_id}'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💬 Открыть чат-рулетку 1 на 1', web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text='👑 Купить подписку PRO', callback_data='menu_vip')],
        [InlineKeyboardButton(text='📤 Поделиться ссылкой', switch_inline_query=f'
💌 Отправь мне анонимное сообщение по ссылке:
{link}')],
        [InlineKeyboardButton(text='💘 Кто в тебя влюблён (Crush-тест)', callback_data='menu_crush')],
        [InlineKeyboardButton(text='📜 Правила сервиса', callback_data='menu_terms')]
    ])
    await message.answer(
        f'👋 <b>Добро пожаловать!</b> Хочешь пообщаться анонимно 1 на 1 в мини-приложении? Нажми кнопку ниже (если передумаешь — всегда можно открыть его командой /chat).

'
        f'🔗 <b>Твоя личная ссылка для анонимных сообщений:</b>
'
        f'{link}

'
        f'Размести её в профиле или сторис, чтобы получать тайные признания!',
        parse_mode='HTML',
        reply_markup=kb
    )

async def deliver_to_target(message: types.Message, target_id: int, msg_db_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='↩️ Ответить анонимно', callback_data=f'reply_{msg_db_id}')],
        [
            InlineKeyboardButton(text='🔍 Подсказка (89 ₽)', callback_data=f'hint_{msg_db_id}'),
            InlineKeyboardButton(text='🕵️‍♂️ Узнать точно (99 ₽)', callback_data=f'reveal_{msg_db_id}')
        ]
    ])
    try:
        await bot.send_message(target_id, '💌 <b>Тебе пришло новое анонимное сообщение!</b>', parse_mode='HTML')
        await bot.copy_message(chat_id=target_id, from_chat_id=message.chat.id, message_id=message.message_id, reply_markup=kb)
        await message.answer('✅ Сообщение успешно доставлено анонимно!')
    except Exception:
        await message.answer('❌ Не удалось доставить сообщение (получатель заблокировал бота).')

# ================= КОМАНДЫ БОТА =================
@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    is_new = register_user(message.from_user)
    if is_new:
        asyncio.create_task(delayed_welcome_message(message.from_user.id))

    args = command.args
    if args and args.isdigit():
        target_id = int(args)
        if target_id == message.from_user.id:
            await message.answer('😅 Нельзя отправить анонимное сообщение самому себе!')
            await send_personal_link(message, message.from_user.id)
            return
        user_states[message.from_user.id] = {'mode': 'send', 'target_id': target_id}
        await message.answer(
            '✍️ <b>Напиши своё анонимное сообщение:</b>
'
            'Можно отправить текст, фото, видео, голосовое или стикер.',
            parse_mode='HTML'
        )
    else:
        await send_personal_link(message, message.from_user.id)

@dp.message(Command('link'))
async def cmd_link(message: types.Message):
    register_user(message.from_user)
    await send_personal_link(message, message.from_user.id)

@dp.message(Command('chat'))
async def cmd_chat(message: types.Message):
    register_user(message.from_user)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⚡ Открыть чат-рулетку 1 на 1', web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text='👑 Подписка PRO / 3 дня бесплатно', callback_data='menu_vip')]
    ])
    await message.answer(
        '💬 <b>Анонимный чат 1 на 1</b>

'
        'Общайтесь со случайными собеседниками в мини-приложении. Без подписки доступно <b>20 собеседников</b> каждые 24 часа.',
        parse_mode='HTML',
        reply_markup=kb
    )

@dp.message(Command('vip'))
async def cmd_vip(message: types.Message):
    register_user(message.from_user)
    await show_vip_menu(message, message.from_user.id)

@dp.callback_query(F.data == 'menu_vip')
async def cb_vip(callback: types.CallbackQuery):
    await show_vip_menu(callback.message, callback.from_user.id)
    await callback.answer()

async def show_vip_menu(message: types.Message, user_id: int):
    q = get_user_quota(user_id)
    rows = []
    if not q['trial_used'] and not q['is_pro']:
        rows.append([InlineKeyboardButton(text='🎁 Попробовать 3 дня бесплатно', callback_data='activate_trial')])
    rows.extend([
        [InlineKeyboardButton(text='👑 1 месяц PRO — 199 ₽', callback_data='sub_1m')],
        [InlineKeyboardButton(text='🔥 6 месяцев PRO — 499 ₽', callback_data='sub_6m')],
        [InlineKeyboardButton(text='💎 1 год PRO — 799 ₽', callback_data='sub_1y')],
    ])
    status_line = '✅ У вас активна подписка PRO!' if q['is_pro'] else f'📊 Осталось бесплатных чатов сегодня: <b>{max(0, 20 - q["used"])}/20</b>'
    await message.answer(
        f'👑 <b>Подписка PRO для чат-рулетки 1 на 1</b>

'
        f'{status_line}

'
        f'Подписка снимает ограничение в 20 собеседников за 24 часа.',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )

@dp.callback_query(F.data == 'activate_trial')
async def cb_activate_trial(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    q = get_user_quota(user_id)
    if q['trial_used'] or q['is_pro']:
        await callback.answer('Пробный период уже использован или у вас активна подписка.', show_alert=True)
        return
    pro_until = int(time.time()) + 3 * 86400
    cursor.execute('UPDATE users SET pro_until = ?, trial_used = 1 WHERE user_id = ?', (pro_until, user_id))
    conn.commit()
    await callback.message.edit_text(
        '🎉 <b>Пробный период на 3 дня активирован!</b>

'
        'Вам открыт безлимитный доступ к мини-приложению 1 на 1 (/chat).',
        parse_mode='HTML'
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('sub_'))
async def cb_buy_sub(callback: types.CallbackQuery):
    plan_key = callback.data.split('_')[1]
    plan = SUB_PLANS.get(plan_key)
    if not plan:
        await callback.answer()
        return
    await send_sub_invoice(callback.from_user.id, plan_key)
    await callback.answer()

async def send_sub_invoice(user_id: int, plan_key: str):
    plan = SUB_PLANS[plan_key]
    await bot.send_invoice(
        chat_id=user_id,
        title=plan['title'],
        description='Безлимитное общение 1 на 1 в мини-приложении без ожидания 24 часов.',
        payload=f'paysub_{plan_key}_{user_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label=plan['title'], amount=plan['price'])]
    )

@dp.message(Command('terms'))
async def cmd_terms(message: types.Message):
    await message.answer(
        '📜 <b>Правила сервиса</b>

'
        '1. Запрещены оскорбления, травля, угрозы и спам.
'
        '2. Цифровые услуги (подсказки, раскрытие, защита, подписка PRO) оказываются мгновенно после оплаты.
'
        '3. Если отправитель оплатил защиту конфиденциальности, его данные не раскрываются, а списание средств при попытке раскрытия отменяется.',
        parse_mode='HTML'
    )

@dp.callback_query(F.data == 'menu_terms')
async def cb_terms(callback: types.CallbackQuery):
    await cmd_terms(callback.message)
    await callback.answer()

@dp.message(Command('crush'))
async def cmd_crush(message: types.Message):
    register_user(message.from_user)
    user_states[message.from_user.id] = {'mode': 'crush'}
    await message.answer(
        '💘 <b>Crush-тест на взаимную симпатию</b>

'
        'Отправь @username человека, который тебе нравится. Если он тоже укажет тебя — вы оба получите уведомление!',
        parse_mode='HTML'
    )

@dp.callback_query(F.data == 'menu_crush')
async def cb_crush(callback: types.CallbackQuery):
    register_user(callback.from_user)
    user_states[callback.from_user.id] = {'mode': 'crush'}
    await callback.message.answer(
        '💘 <b>Crush-тест на взаимную симпатию</b>

'
        'Отправь @username человека, который тебе нравится. Если он тоже укажет тебя — вы оба получите уведомление!',
        parse_mode='HTML'
    )
    await callback.answer()

# ================= ОБРАБОТКА ВХОДЯЩИХ СООБЩЕНИЙ =================
@dp.message()
async def handle_messages(message: types.Message):
    if message.successful_payment:
        return
    user_id = message.from_user.id
    register_user(message.from_user)
    state = user_states.get(user_id)

    if not state:
        await send_personal_link(message, user_id)
        return

    if state.get('mode') == 'crush':
        raw = (message.text or '').strip().lstrip('@').lower()
        if not raw or ' ' in raw or len(raw) < 3:
            await message.answer('⚠️ Отправь корректный @username (например: @durov).')
            return
        cursor.execute('INSERT OR REPLACE INTO crushes (user_id, crush_username) VALUES (?, ?)', (user_id, raw))
        conn.commit()
        user_states.pop(user_id, None)

        my_uname = (message.from_user.username or '').lower()
        if my_uname:
            cursor.execute('SELECT user_id FROM users WHERE username = ?', (raw,))
            target_row = cursor.fetchone()
            if target_row:
                target_uid = target_row[0]
                cursor.execute('SELECT crush_username FROM crushes WHERE user_id = ?', (target_uid,))
                crush_row = cursor.fetchone()
                if crush_row and crush_row[0] == my_uname:
                    kb1 = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text='🔥 Открыть взаимную симпатию (89 ₽)', callback_data=f'paycrush_{target_uid}')]
                    ])
                    kb2 = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text='🔥 Открыть взаимную симпатию (89 ₽)', callback_data=f'paycrush_{user_id}')]
                    ])
                    await message.answer('💘 <b>Совпадение!</b> Твоя симпатия взаимна!', parse_mode='HTML', reply_markup=kb1)
                    try:
                        await bot.send_message(target_uid, '💘 <b>Совпадение в Crush-тесте!</b> Твоя симпатия взаимна!', parse_mode='HTML', reply_markup=kb2)
                    except Exception:
                        pass
                    return
        await message.answer(f'✅ Симпатия к @{raw} сохранена! Мы сразу сообщим, если она окажется взаимной.')
        return

    if state.get('mode') == 'send':
        target_id = state['target_id']
        user_states.pop(user_id, None)

        if is_dialog_protected(user_id, target_id):
            msg_db_id = save_message_to_db(message.from_user, target_id, is_protected=1)
            await deliver_to_target(message, target_id, msg_db_id)
            return

        msg_db_id = save_message_to_db(message.from_user, target_id, is_protected=0)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='📨 Отправить бесплатно', callback_data=f'freesend_{msg_db_id}')],
            [InlineKeyboardButton(text='🛡 Защитить от раскрытия (119 ₽)', callback_data=f'protectsend_{msg_db_id}')]
        ])
        await message.answer(
            '🛡 <b>Защитить твоё сообщение?</b>

'
            'Получатель может оплатить раскрытие и узнать твой аккаунт. С защитой за <b>119 ₽</b> твой диалог будет навсегда скрыт!',
            parse_mode='HTML',
            reply_markup=kb
        )

@dp.callback_query(F.data.startswith('reply_'))
async def process_reply_btn(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    cursor.execute('SELECT sender_id FROM messages WHERE id = ?', (msg_db_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer('Сообщение не найдено.', show_alert=True)
        return
    user_states[callback.from_user.id] = {'mode': 'send', 'target_id': row[0]}
    await callback.message.answer('↩️ <b>Напиши анонимный ответ:</b>', parse_mode='HTML')
    await callback.answer()

@dp.callback_query(F.data.startswith('freesend_'))
async def process_free_send(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    cursor.execute('SELECT target_id FROM messages WHERE id = ?', (msg_db_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer('Сообщение устарело.', show_alert=True)
        return
    await callback.message.edit_text('📨 Отправляем сообщение...')
    await deliver_to_target(callback.message.reply_to_message or callback.message, row[0], msg_db_id)
    await callback.answer()

@dp.callback_query(F.data.startswith('protectsend_'))
async def process_protect_send(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='🛡 Защита конфиденциальности диалога',
        description='Гарантирует, что получатель не сможет узнать вас даже за деньги.',
        payload=f'payprotect_{msg_db_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Защита диалога', amount=PRICE_PROTECT)]
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('urgentprotect_'))
async def process_urgent_protect(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    urgent_pending[msg_db_id] = time.time()
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='🚨 Экстренная защита от раскрытия',
        description='Мгновенно блокирует попытку получателя узнать ваш профиль.',
        payload=f'payurgent_{msg_db_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Экстренная защита', amount=PRICE_PROTECT)]
    )
    await callback.answer()

async def trigger_sender_alert(msg_db_id: int, sender_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🚨 Экстренно защитить (119 ₽)', callback_data=f'urgentprotect_{msg_db_id}')]
    ])
    try:
        await bot.send_message(
            sender_id,
            '🚨 <b>Внимание! Получатель прямо сейчас пытается раскрыть тебя!</b>
'
            'Успей активировать экстренную защиту за <b>119 ₽</b>, чтобы скрыть свой аккаунт!',
            parse_mode='HTML',
            reply_markup=kb
        )
    except Exception:
        pass

@dp.callback_query(F.data.startswith('hint_'))
async def process_hint_request(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    cursor.execute('SELECT sender_id, target_id, sender_name, sender_lang FROM messages WHERE id = ?', (msg_db_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer('Сообщение не найдено.', show_alert=True)
        return
    sender_id, target_id, sender_name, sender_lang = row

    if is_dialog_protected(sender_id, target_id, msg_db_id):
        await callback.answer('😶‍🌫️ Сообщение находится под защитой конфиденциальности. Оплата не требуется.', show_alert=True)
        return

    cursor.execute('SELECT 1 FROM unlocked_messages WHERE user_id = ? AND msg_id = ? AND unlock_type = ?', (callback.from_user.id, msg_db_id, 'hint'))
    if cursor.fetchone():
        first_letter = (sender_name or '?')[0].upper()
        await callback.message.answer(
            f'🔍 <b>Подсказка об отправителе:</b>
'
            f'• Первая буква имени: <b>{html.escape(first_letter)}</b>
'
            f'• Регистрация аккаунта: <b>{estimate_reg_year(sender_id)}</b>
'
            f'• Язык интерфейса: <b>{html.escape(sender_lang or "неизвестен")}</b>',
            parse_mode='HTML'
        )
        await callback.answer()
        return

    asyncio.create_task(trigger_sender_alert(msg_db_id, sender_id))
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='🔍 Подсказка об отправителе',
        description='Первая буква имени, примерный год регистрации и язык Telegram.',
        payload=f'payhint_{msg_db_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Подсказка', amount=PRICE_HINT)]
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('reveal_'))
async def process_reveal_request(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    cursor.execute('SELECT sender_id, target_id, sender_username, sender_name FROM messages WHERE id = ?', (msg_db_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer('Сообщение не найдено.', show_alert=True)
        return
    sender_id, target_id, sender_username, sender_name = row

    if is_dialog_protected(sender_id, target_id, msg_db_id):
        await callback.answer('😶‍🌫️ Сообщение находится под защитой конфиденциальности. Оплата не требуется.', show_alert=True)
        return

    cursor.execute('SELECT 1 FROM unlocked_messages WHERE user_id = ? AND msg_id = ? AND unlock_type = ?', (callback.from_user.id, msg_db_id, 'reveal'))
    if cursor.fetchone():
        safe_name = html.escape(sender_name or 'Пользователь')
        await callback.message.answer(
            f'🕵️‍♂️ <b>Отправитель раскрыт:</b>
'
            f'• Имя: <a href="tg://user?id={sender_id}">{safe_name}</a>
'
            f'• Username: <b>{html.escape(sender_username or "скрыт")}</b>
'
            f'• ID: <code>{sender_id}</code>',
            parse_mode='HTML'
        )
        await callback.answer()
        return

    asyncio.create_task(trigger_sender_alert(msg_db_id, sender_id))
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='🕵️‍♂️ Полное раскрытие отправителя',
        description='Показывает имя, @username и прямую ссылку на профиль.',
        payload=f'payreveal_{msg_db_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Раскрытие автора', amount=PRICE_REVEAL)]
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('paycrush_'))
async def process_crush_pay(callback: types.CallbackQuery):
    match_uid = int(callback.data.split('_')[1])
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='💘 Открыть взаимную симпатию',
        description='Узнай, с кем у тебя совпала симпатия в Crush-тесте.',
        payload=f'paycrush_{match_uid}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Взаимная симпатия', amount=PRICE_CRUSH)]
    )
    await callback.answer()

# ================= ПРОВЕРКА ПЕРЕД СПИСАНИЕМ СРЕДСТВ =================
@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    payload = query.invoice_payload
    if payload.startswith(('payhint_', 'payreveal_')):
        msg_db_id = int(payload.split('_')[1])
        cursor.execute('SELECT sender_id, target_id FROM messages WHERE id = ?', (msg_db_id,))
        row = cursor.fetchone()
        if row and is_dialog_protected(row[0], row[1], msg_db_id):
            await bot.answer_pre_checkout_query(
                query.id,
                ok=False,
                error_message='Отправитель активировал защиту конфиденциальности! Оплата отменена, деньги не списаны.'
            )
            return
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    sp = message.successful_payment
    payload = sp.invoice_payload
    user_id = message.from_user.id
    now = int(time.time())

    try:
        if payload.startswith('paysub_'):
            _, plan_key, _ = payload.split('_')
            plan = SUB_PLANS[plan_key]
            cursor.execute('SELECT pro_until FROM users WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            base_time = max(now, (row[0] if row and row[0] else 0))
            new_until = base_time + plan['days'] * 86400
            cursor.execute('UPDATE users SET pro_until = ?, trial_used = 1 WHERE user_id = ?', (new_until, user_id))
            cursor.execute('INSERT INTO payments (user_id, category, amount, created_at) VALUES (?, ?, ?, ?)', (user_id, f'sub_{plan_key}', sp.total_amount, now))
            conn.commit()
            await message.answer(
                f'👑 <b>{plan["title"]} успешно активирована!</b>

'
                f'Теперь вам доступен полный безлимит в чат-рулетке (/chat).',
                parse_mode='HTML'
            )

        elif payload.startswith(('payprotect_', 'payurgent_')):
            msg_db_id = int(payload.split('_')[1])
            cursor.execute('SELECT sender_id, target_id FROM messages WHERE id = ?', (msg_db_id,))
            row = cursor.fetchone()
            if row:
                cursor.execute('UPDATE messages SET is_protected = 1 WHERE sender_id = ? AND target_id = ?', (row[0], row[1]))
            cursor.execute('INSERT INTO payments (user_id, category, amount, created_at) VALUES (?, ?, ?, ?)', (user_id, 'protect', sp.total_amount, now))
            conn.commit()
            if payload.startswith('payprotect_') and row:
                await message.answer('🛡 <b>Защита диалога активирована!</b> Отправляем сообщение...')
                await deliver_to_target(message, row[1], msg_db_id)
            else:
                await message.answer('🚨 <b>Экстренная защита активирована!</b> Получатель не сможет узнать твой профиль.')

        elif payload.startswith('payhint_'):
            msg_db_id = int(payload.split('_')[1])
            cursor.execute('SELECT sender_id, target_id, sender_name, sender_lang FROM messages WHERE id = ?', (msg_db_id,))
            row = cursor.fetchone()
            if not row:
                return
            sender_id, target_id, sender_name, sender_lang = row
            if is_dialog_protected(sender_id, target_id, msg_db_id):
                await message.answer('😶‍🌫️ Извините, сообщение оплачено и считается конфиденциальным 😶‍🌫️')
                return
            cursor.execute('INSERT OR IGNORE INTO unlocked_messages VALUES (?, ?, ?)', (user_id, msg_db_id, 'hint'))
            cursor.execute('INSERT INTO payments (user_id, category, amount, created_at) VALUES (?, ?, ?, ?)', (user_id, 'hint', sp.total_amount, now))
            conn.commit()
            first_letter = (sender_name or '?')[0].upper()
            await message.answer(
                f'🔍 <b>Подсказка об отправителе:</b>
'
                f'• Первая буква имени: <b>{html.escape(first_letter)}</b>
'
                f'• Регистрация аккаунта: <b>{estimate_reg_year(sender_id)}</b>
'
                f'• Язык интерфейса: <b>{html.escape(sender_lang or "неизвестен")}</b>',
                parse_mode='HTML'
            )

        elif payload.startswith('payreveal_'):
            msg_db_id = int(payload.split('_')[1])
            cursor.execute('SELECT sender_id, target_id, sender_username, sender_name FROM messages WHERE id = ?', (msg_db_id,))
            row = cursor.fetchone()
            if not row:
                return
            sender_id, target_id, sender_username, sender_name = row
            if is_dialog_protected(sender_id, target_id, msg_db_id):
                await message.answer('😶‍🌫️ Извините, сообщение оплачено и считается конфиденциальным 😶‍🌫️')
                return
            cursor.execute('INSERT OR IGNORE INTO unlocked_messages VALUES (?, ?, ?)', (user_id, msg_db_id, 'reveal'))
            cursor.execute('INSERT INTO payments (user_id, category, amount, created_at) VALUES (?, ?, ?, ?)', (user_id, 'reveal', sp.total_amount, now))
            conn.commit()
            safe_name = html.escape(sender_name or 'Пользователь')
            await message.answer(
                f'🕵️‍♂️ <b>Отправитель раскрыт:</b>
'
                f'• Имя: <a href="tg://user?id={sender_id}">{safe_name}</a>
'
                f'• Username: <b>{html.escape(sender_username or "скрыт")}</b>
'
                f'• ID: <code>{sender_id}</code>',
                parse_mode='HTML'
            )

        elif payload.startswith('paycrush_'):
            match_uid = int(payload.split('_')[1])
            cursor.execute('SELECT username, full_name FROM users WHERE user_id = ?', (match_uid,))
            row = cursor.fetchone()
            cursor.execute('INSERT INTO payments (user_id, category, amount, created_at) VALUES (?, ?, ?, ?)', (user_id, 'crush', sp.total_amount, now))
            conn.commit()
            uname = f'@{row[0]}' if row and row[0] else 'скрыт'
            fname = html.escape(row[1] if row and row[1] else 'Пользователь')
            await message.answer(
                f'💘 <b>Твоя взаимная симпатия:</b>
'
                f'• <a href="tg://user?id={match_uid}">{fname}</a> ({uname})',
                parse_mode='HTML'
            )
    except Exception as e:
        logging.error(f'Payment error: {e}')

# ================= АДМИН-ПАНЕЛЬ =================
@dp.message(Command('admin'))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    now = int(time.time())
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM users WHERE pro_until > ?', (now,))
    active_pro = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM users WHERE trial_used = 1')
    trials_used = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM messages')
    total_msgs = cursor.fetchone()[0]
    cursor.execute('SELECT COALESCE(SUM(amount), 0) FROM payments')
    total_rev = cursor.fetchone()[0] // 100
    cursor.execute('SELECT COALESCE(SUM(amount), 0) FROM payments WHERE category LIKE "sub_%"')
    sub_rev = cursor.fetchone()[0] // 100

    await message.answer(
        f'📊 <b>Статистика проекта</b>

'
        f'👥 Всего пользователей: <b>{total_users}</b>
'
        f'🟢 Онлайн в Mini App: <b>{len(active_ws)}</b>
'
        f'👑 Активных PRO-подписок: <b>{active_pro}</b>
'
        f'🎁 Использовано триалов (3 дня): <b>{trials_used}</b>
'
        f'💌 Анонимных сообщений: <b>{total_msgs}</b>

'
        f'💰 Общая выручка: <b>{total_rev} ₽</b>
'
        f'💳 Из них с подписок PRO: <b>{sub_rev} ₽</b>',
        parse_mode='HTML'
    )

# ================= ВЕБ-СЕРВЕР И WEBSOCKET ЧАТ-РУЛЕТКИ =================
active_ws = {}     # user_id -> WebSocketResponse
waiting_queue = [] # list of user_id
active_pairs = {}  # user_id -> peer_id

async def broadcast_online():
    count = max(1, len(active_ws))
    for uid, ws in list(active_ws.items()):
        try:
            q = get_user_quota(uid)
            await ws.send_json({'type': 'status', 'online': count, **q})
        except Exception:
            pass

def disconnect_pair(uid: int, notify_peer: bool = True):
    if uid in waiting_queue:
        waiting_queue.remove(uid)
    peer_id = active_pairs.pop(uid, None)
    if peer_id:
        active_pairs.pop(peer_id, None)
        if notify_peer and peer_id in active_ws:
            asyncio.create_task(active_ws[peer_id].send_json({'type': 'peer_left'}))

async def match_user(uid: int):
    disconnect_pair(uid, notify_peer=True)
    if not consume_chat_quota(uid):
        q = get_user_quota(uid)
        if uid in active_ws:
            await active_ws[uid].send_json({'type': 'limit_reached', 'online': len(active_ws), **q})
        return

    await broadcast_online()
    while waiting_queue:
        peer_id = waiting_queue.pop(0)
        if peer_id != uid and peer_id in active_ws and peer_id not in active_pairs:
            active_pairs[uid] = peer_id
            active_pairs[peer_id] = uid
            await active_ws[uid].send_json({'type': 'matched'})
            await active_ws[peer_id].send_json({'type': 'matched'})
            return

    waiting_queue.append(uid)
    if uid in active_ws:
        await active_ws[uid].send_json({'type': 'waiting'})

async def index_handler(request: web.Request):
    return web.FileResponse('index.html')

async def ws_handler(request: web.Request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    try:
        uid = int(request.query.get('user_id', '0'))
    except ValueError:
        uid = 0

    if uid:
        active_ws[uid] = ws
        await broadcast_online()

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                act = data.get('action')
                if act == 'find':
                    await match_user(uid)
                elif act == 'cancel':
                    if uid in waiting_queue:
                        waiting_queue.remove(uid)
                elif act == 'next':
                    await match_user(uid)
                elif act == 'stop':
                    disconnect_pair(uid, notify_peer=True)
                elif act == 'msg':
                    peer_id = active_pairs.get(uid)
                    txt = str(data.get('text', '')).strip()[:1000]
                    if peer_id and txt and peer_id in active_ws:
                        await active_ws[peer_id].send_json({'type': 'msg', 'text': txt})
                elif act == 'trial':
                    q = get_user_quota(uid)
                    if not q['trial_used'] and not q['is_pro']:
                        pro_until = int(time.time()) + 3 * 86400
                        cursor.execute('UPDATE users SET pro_until = ?, trial_used = 1 WHERE user_id = ?', (pro_until, uid))
                        conn.commit()
                    await broadcast_online()
                elif act == 'buy_sub':
                    plan_key = data.get('plan')
                    if plan_key in SUB_PLANS:
                        await send_sub_invoice(uid, plan_key)
                        await ws.send_json({'type': 'invoice_sent'})
    finally:
        if uid:
            disconnect_pair(uid, notify_peer=True)
            active_ws.pop(uid, None)
            await broadcast_online()
    return ws

async def main():
    app = web.Application()
    app.router.add_get('/', index_handler)
    app.router.add_get('/ws', ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f'Mini App server running on port {PORT}')
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
