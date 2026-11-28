import os
import time
import json
import html
import sqlite3
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, WebAppInfo

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv('BOT_TOKEN')
PROVIDER_TOKEN = os.getenv('PROVIDER_TOKEN', '381764678:TEST:101645')
ADMIN_ID = 8263206496
WEBAPP_URL = os.getenv('WEBAPP_URL', 'https://bot-1795118631-6172-ramych.bothost.tech')
PORT = int(os.getenv('PORT', 3000))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

conn = sqlite3.connect('anon_bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    pro_until INTEGER DEFAULT 0,
    trial_used INTEGER DEFAULT 0,
    daily_chats INTEGER DEFAULT 0,
    last_reset INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

for col_sql in [
    'ALTER TABLE users ADD COLUMN pro_until INTEGER DEFAULT 0',
    'ALTER TABLE users ADD COLUMN trial_used INTEGER DEFAULT 0',
    'ALTER TABLE users ADD COLUMN daily_chats INTEGER DEFAULT 0',
    'ALTER TABLE users ADD COLUMN last_reset INTEGER DEFAULT 0'
]:
    try:
        cursor.execute(col_sql)
    except sqlite3.OperationalError:
        pass

cursor.execute('''
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER,
    sender_username TEXT,
    sender_name TEXT,
    sender_lang TEXT,
    target_id INTEGER,
    is_protected INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS protected_pairs (
    sender_id INTEGER,
    target_id INTEGER,
    PRIMARY KEY (sender_id, target_id)
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

cursor.execute('''
CREATE TABLE IF NOT EXISTS crushes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    my_username TEXT,
    target_username TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    pay_type TEXT,
    amount INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')
conn.commit()

user_states = {}
pending_messages = {}
active_ws = {}
waiting_queue = []
chat_pairs = {}

def register_user(user_id: int) -> bool:
    cursor.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,))
    if cursor.fetchone():
        return False
    cursor.execute('INSERT INTO users (user_id, last_reset) VALUES (?, ?)', (user_id, int(time.time())))
    conn.commit()
    return True

def get_sub_status(user_id: int):
    register_user(user_id)
    now = int(time.time())
    cursor.execute('SELECT pro_until, trial_used, daily_chats, last_reset FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        return 0, 0, 0, now
    pro_until, trial_used, daily_chats, last_reset = row
    if now - (last_reset or 0) >= 86400:
        daily_chats = 0
        last_reset = now
        cursor.execute('UPDATE users SET daily_chats = 0, last_reset = ? WHERE user_id = ?', (now, user_id))
        conn.commit()
    return pro_until or 0, trial_used or 0, daily_chats or 0, last_reset or now

def is_pair_protected(sender_id: int, target_id: int) -> bool:
    cursor.execute('SELECT 1 FROM protected_pairs WHERE sender_id = ? AND target_id = ?', (sender_id, target_id))
    if cursor.fetchone():
        return True
    cursor.execute('SELECT 1 FROM messages WHERE sender_id = ? AND target_id = ? AND is_protected = 1 LIMIT 1', (sender_id, target_id))
    return bool(cursor.fetchone())

def set_pair_protected(sender_id: int, target_id: int):
    cursor.execute('INSERT OR IGNORE INTO protected_pairs (sender_id, target_id) VALUES (?, ?)', (sender_id, target_id))
    cursor.execute('UPDATE messages SET is_protected = 1 WHERE sender_id = ? AND target_id = ?', (sender_id, target_id))
    conn.commit()

def is_msg_unlocked(user_id: int, msg_id: int, unlock_type: str) -> bool:
    cursor.execute('SELECT 1 FROM unlocked_messages WHERE user_id = ? AND msg_id = ? AND unlock_type = ?', (user_id, msg_id, unlock_type))
    return bool(cursor.fetchone())

def mark_msg_unlocked(user_id: int, msg_id: int, unlock_type: str):
    cursor.execute('INSERT OR IGNORE INTO unlocked_messages (user_id, msg_id, unlock_type) VALUES (?, ?, ?)', (user_id, msg_id, unlock_type))
    conn.commit()

def save_message_to_db(sender: types.User, target_id: int, is_protected: int = 0) -> int:
    username = f'@{sender.username}' if sender.username else 'скрыт'
    full_name = sender.full_name or 'Без имени'
    lang = sender.language_code or 'неизвестен'
    if is_protected == 1 or is_pair_protected(sender.id, target_id):
        is_protected = 1
        set_pair_protected(sender.id, target_id)
    cursor.execute(
        'INSERT INTO messages (sender_id, sender_username, sender_name, sender_lang, target_id, is_protected) VALUES (?, ?, ?, ?, ?, ?)',
        (sender.id, username, full_name, lang, target_id, is_protected)
    )
    conn.commit()
    return cursor.lastrowid

async def handle_index(request):
    if os.path.exists('index.html'):
        return web.FileResponse('index.html')
    return web.Response(text='index.html not found', status=404)

async def send_ws_state(uid: int):
    ws = active_ws.get(uid)
    if not ws or ws.closed:
        return
    pro_until, trial_used, daily_chats, last_reset = get_sub_status(uid)
    now = int(time.time())
    is_pro = pro_until > now
    reset_in = max(0, 86400 - (now - last_reset))
    await ws.send_json({
        'type': 'state',
        'online': len(active_ws),
        'is_pro': is_pro,
        'pro_until': pro_until,
        'trial_used': bool(trial_used),
        'chats_left': 999 if is_pro else max(0, 20 - daily_chats),
        'reset_in': reset_in
    })

async def broadcast_online():
    count = len(active_ws)
    for ws in list(active_ws.values()):
        if not ws.closed:
            try:
                await ws.send_json({'type': 'online', 'count': count})
            except Exception:
                pass

async def disconnect_pair(uid: int, notify_partner: bool = True):
    partner_id = chat_pairs.pop(uid, None)
    if partner_id:
        chat_pairs.pop(partner_id, None)
        if notify_partner and partner_id in active_ws and not active_ws[partner_id].closed:
            try:
                await active_ws[partner_id].send_json({'type': 'partner_left'})
            except Exception:
                pass

async def handle_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    uid = None
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                action = data.get('action')
                if action == 'init':
                    uid = int(data.get('user_id', 0))
                    if uid:
                        active_ws[uid] = ws
                        await send_ws_state(uid)
                        await broadcast_online()
                elif action == 'find' and uid:
                    await disconnect_pair(uid, notify_partner=True)
                    if uid in waiting_queue:
                        waiting_queue.remove(uid)
                    pro_until, _, daily_chats, last_reset = get_sub_status(uid)
                    now = int(time.time())
                    if pro_until <= now and daily_chats >= 20:
                        await ws.send_json({'type': 'limit_reached', 'reset_in': max(0, 86400 - (now - last_reset))})
                        continue
                    partner_id = None
                    while waiting_queue:
                        cand = waiting_queue.pop(0)
                        if cand != uid and cand in active_ws and not active_ws[cand].closed:
                            partner_id = cand
                            break
                    if partner_id:
                        chat_pairs[uid] = partner_id
                        chat_pairs[partner_id] = uid
                        for p in (uid, partner_id):
                            pu, _, dc, _ = get_sub_status(p)
                            if pu <= now:
                                cursor.execute('UPDATE users SET daily_chats = ? WHERE user_id = ?', (dc + 1, p))
                        conn.commit()
                        await send_ws_state(uid)
                        await send_ws_state(partner_id)
                        await ws.send_json({'type': 'matched'})
                        await active_ws[partner_id].send_json({'type': 'matched'})
                    else:
                        waiting_queue.append(uid)
                        await ws.send_json({'type': 'searching'})
                elif action == 'message' and uid:
                    text = str(data.get('text', '')).strip()
                    partner_id = chat_pairs.get(uid)
                    if text and partner_id and partner_id in active_ws and not active_ws[partner_id].closed:
                        await active_ws[partner_id].send_json({'type': 'message', 'text': text})
                elif action == 'stop' and uid:
                    if uid in waiting_queue:
                        waiting_queue.remove(uid)
                    await disconnect_pair(uid, notify_partner=True)
                    await send_ws_state(uid)
                elif action == 'activate_trial' and uid:
                    _, trial_used, _, _ = get_sub_status(uid)
                    if trial_used == 0:
                        pro_until = int(time.time()) + 3 * 86400
                        cursor.execute('UPDATE users SET pro_until = ?, trial_used = 1 WHERE user_id = ?', (pro_until, uid))
                        conn.commit()
                        await ws.send_json({'type': 'trial_activated'})
                    await send_ws_state(uid)
                elif action == 'buy_pro' and uid:
                    plan = data.get('plan', '1m')
                    plans = {
                        '1m': ('PRO-подписка на 1 месяц', 'Безлимитное общение в чат-рулетке 1 на 1 на 30 дней', 19900),
                        '6m': ('PRO-подписка на 6 месяцев', 'Безлимитное общение в чат-рулетке 1 на 1 на 180 дней', 49900),
                        '1y': ('PRO-подписка на 1 год', 'Безлимитное общение в чат-рулетке 1 на 1 на 365 дней', 79900),
                    }
                    if plan in plans:
                        title, desc, amount = plans[plan]
                        try:
                            await bot.send_invoice(
                                chat_id=uid,
                                title=title,
                                description=desc,
                                payload=f'paypro_{plan}_{uid}',
                                provider_token=PROVIDER_TOKEN,
                                currency='RUB',
                                prices=[LabeledPrice(label=title, amount=amount)]
                            )
                            await ws.send_json({'type': 'invoice_sent'})
                        except Exception as e:
                            logging.error(f'Invoice error: {e}')
    finally:
        if uid:
            if uid in waiting_queue:
                waiting_queue.remove(uid)
            await disconnect_pair(uid, notify_partner=True)
            active_ws.pop(uid, None)
            await broadcast_online()
    return ws

web_app = web.Application()
web_app.router.add_get('/', handle_index)
web_app.router.add_get('/ws', handle_ws)

async def send_delayed_welcome(user_id: int):
    await asyncio.sleep(120)
    cursor.execute('SELECT COUNT(*) FROM messages WHERE target_id = ?', (user_id,))
    row = cursor.fetchone()
    if row and row[0] > 0:
        return
    try:
        await bot.send_message(
            user_id,
            text='💌 <b>Новое сообщение от тайного собеседника!</b>\n\n«👋 Привет! Я твой первый анонимный собеседник. Скорее выложи свою ссылку в сторис или отправь друзьям — уже через пару минут тебе начнут прилетать честные вопросы и признания!»',
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
        [InlineKeyboardButton(text='📤 Поделиться ссылкой', switch_inline_query=f'\n💌 Отправь мне анонимное сообщение по ссылке:\n{link}')],
        [InlineKeyboardButton(text='💘 Кто в тебя влюблён (Crush-тест)', callback_data='menu_crush')],
        [InlineKeyboardButton(text='📜 Правила сервиса', callback_data='menu_terms')]
    ])
    await message.answer(
        f'👋 <b>Добро пожаловать!</b> Хочешь пообщаться анонимно 1 на 1 в мини-приложении? Нажми кнопку ниже (если передумаешь — всегда можно открыть его командой /chat).\n\n🔗 <b>Твоя личная ссылка для анонимных сообщений:</b>\n\n{link}\n\nРазмести её в профиле или сторис, чтобы получать тайные признания!',
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
    await bot.send_message(target_id, '💌 <b>Тебе пришло новое анонимное сообщение!</b>', parse_mode='HTML')
    await message.copy_to(chat_id=target_id, reply_markup=kb)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    is_new = register_user(message.from_user.id)
    if is_new:
        asyncio.create_task(send_delayed_welcome(message.from_user.id))
    parts = message.text.split(maxsplit=1)
    args = parts[1].strip() if len(parts) > 1 else ''
    if args and args.isdigit():
        target_id = int(args)
        if target_id == message.from_user.id:
            await message.answer('😅 Нельзя отправить анонимное сообщение самому себе!\n\nВот твоя ссылка для друзей:')
            await send_personal_link(message, message.from_user.id)
        else:
            user_states[message.from_user.id] = {'mode': 'anon_msg', 'target_id': target_id}
            await message.answer(
                '✍️ <b>Напиши своё анонимное сообщение:</b>\n\nОтправь текст, фото, видео, голосовое или стикер — получатель не узнает, кто ты!',
                parse_mode='HTML'
            )
    else:
        await send_personal_link(message, message.from_user.id)

@dp.message(Command('chat'))
async def cmd_chat(message: types.Message):
    register_user(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💬 Открыть чат-рулетку 1 на 1', web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text='👑 Купить подписку PRO', callback_data='menu_vip')]
    ])
    await message.answer('💬 <b>Анонимный чат 1 на 1</b>\n\nНажми кнопку ниже, чтобы запустить мини-приложение и найти случайного собеседника:', parse_mode='HTML', reply_markup=kb)

@dp.message(Command('vip'))
async def cmd_vip(message: types.Message):
    await send_vip_menu(message)

async def send_vip_menu(target_msg: types.Message):
    user_id = target_msg.chat.id
    pro_until, trial_used, _, _ = get_sub_status(user_id)
    now = int(time.time())
    if pro_until > now:
        dt = time.strftime('%d.%m.%Y %H:%M', time.localtime(pro_until))
        await target_msg.answer(f'👑 <b>У тебя уже активна подписка PRO!</b>\n\nБезлимитный доступ в чат-рулетке действует до: <b>{dt}</b>', parse_mode='HTML')
        return
    rows = []
    if trial_used == 0:
        rows.append([InlineKeyboardButton(text='🎁 Попробовать 3 дня бесплатно', callback_data='activate_trial')])
    rows.extend([
        [InlineKeyboardButton(text='⭐️ 1 месяц — 199 ₽', callback_data='buypro_1m')],
        [InlineKeyboardButton(text='🔥 6 месяцев — 499 ₽ (выгода 58%)', callback_data='buypro_6m')],
        [InlineKeyboardButton(text='👑 1 год — 799 ₽ (выгода 67%)', callback_data='buypro_1y')]
    ])
    await target_msg.answer(
        '👑 <b>PRO-подписка на Анонимный Чат 1 на 1</b>\n\nБесплатно доступно <b>20 собеседников</b> каждые 24 часа.\nС подпиской <b>PRO</b> ты получаешь:\n• ♾ Полный безлимит на общение 24/7\n• ⚡️ Моментальный поиск без ожидания сброса таймера\n\nВыбери удобный тариф:',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )

@dp.callback_query(F.data == 'menu_vip')
async def cb_menu_vip(callback: types.CallbackQuery):
    await send_vip_menu(callback.message)
    await callback.answer()

@dp.callback_query(F.data == 'activate_trial')
async def cb_activate_trial(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    _, trial_used, _, _ = get_sub_status(user_id)
    if trial_used == 1:
        await callback.answer('Пробный период уже был использован ранее!', show_alert=True)
        return
    pro_until = int(time.time()) + 3 * 86400
    cursor.execute('UPDATE users SET pro_until = ?, trial_used = 1 WHERE user_id = ?', (pro_until, user_id))
    conn.commit()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💬 Перейти в чат-рулетку 1 на 1', web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await callback.message.edit_text('🎉 <b>Пробный период PRO на 3 дня активирован!</b>\n\nТеперь у тебя полный безлимит на общение с собеседниками без ограничений в 20 чатов.', parse_mode='HTML', reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith('buypro_'))
async def cb_buy_pro(callback: types.CallbackQuery):
    plan = callback.data.split('_')[1]
    plans = {
        '1m': ('PRO-подписка на 1 месяц', 'Безлимитное общение в анонимном чате 1 на 1 на 30 дней', 19900),
        '6m': ('PRO-подписка на 6 месяцев', 'Безлимитное общение в анонимном чате 1 на 1 на 180 дней', 49900),
        '1y': ('PRO-подписка на 1 год', 'Безлимитное общение в анонимном чате 1 на 1 на 365 дней', 79900),
    }
    if plan not in plans:
        return
    title, desc, amount = plans[plan]
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=title,
        description=desc,
        payload=f'paypro_{plan}_{callback.from_user.id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label=title, amount=amount)]
    )
    await callback.answer()

@dp.message(Command('link'))
async def cmd_link(message: types.Message):
    await send_personal_link(message, message.from_user.id)

@dp.message(Command('terms'))
async def cmd_terms(message: types.Message):
    await message.answer(
        '📜 <b>Правила и условия использования сервиса</b>\n\n1. <b>Анонимность и конфиденциальность:</b> Отправка сообщений и общение в чате 1 на 1 полностью анонимны. Раскрытие данных отправителя возможно только через встроенные платные функции.\n2. <b>Запрещённый контент:</b> Запрещены угрозы, травля, спам и распространение запрещённых материалов.\n3. <b>Цифровые услуги:</b> Оплата подсказок, раскрытия, защиты и PRO-подписок является оплатой цифровых функций сервиса.',
        parse_mode='HTML'
    )

@dp.callback_query(F.data == 'menu_terms')
async def cb_terms(callback: types.CallbackQuery):
    await cmd_terms(callback.message)
    await callback.answer()

@dp.message(Command('crush'))
async def cmd_crush_cmd(message: types.Message):
    user_states[message.from_user.id] = {'mode': 'crush'}
    await message.answer('💘 <b>Тест на взаимную симпатию (Crush-тест)</b>\n\nВведи <code>@username</code> человека, который тебе нравится.\nЕсли он тоже введёт твой <code>@username</code> — бот сообщит о совпадении!\n\n<i>Никто не узнает, кого ты вводил, пока симпатия не станет взаимной.</i>', parse_mode='HTML')

@dp.callback_query(F.data == 'menu_crush')
async def cb_crush(callback: types.CallbackQuery):
    if not callback.from_user.username:
        await callback.answer('Для Crush-теста установи @username в настройках Telegram!', show_alert=True)
        return
    user_states[callback.from_user.id] = {'mode': 'crush'}
    await callback.message.answer('💘 <b>Тест на взаимную симпатию (Crush-тест)</b>\n\nВведи <code>@username</code> человека, который тебе нравится.\nЕсли он тоже введёт твой <code>@username</code> — бот сообщит о совпадении!\n\n<i>Никто не узнает, кого ты вводил, пока симпатия не станет взаимной.</i>', parse_mode='HTML')
    await callback.answer()

@dp.message(F.text & ~F.text.startswith('/'))
@dp.message(F.photo | F.video | F.voice | F.sticker | F.animation | F.document | F.video_note)
async def handle_messages(message: types.Message):
    user_id = message.from_user.id
    register_user(user_id)
    state = user_states.get(user_id)
    if not state:
        await message.answer('Чтобы получать анонимные сообщения, поделись своей личной ссылкой (команда /link) или открой чат-рулетку 1 на 1 (/chat)!')
        return
    if state.get('mode') == 'crush':
        if not message.text:
            await message.answer('Пожалуйста, отправь текстом @username человека (например: @durov).')
            return
        target_username = message.text.strip().lower().lstrip('@')
        my_username = (message.from_user.username or '').lower()
        if not my_username:
            await message.answer('У тебя не установлен @username в Telegram.')
            user_states.pop(user_id, None)
            return
        if target_username == my_username or len(target_username) < 3:
            await message.answer('Введи корректный @username другого человека.')
            return
        cursor.execute('INSERT INTO crushes (user_id, my_username, target_username) VALUES (?, ?, ?)', (user_id, my_username, target_username))
        conn.commit()
        cursor.execute('SELECT user_id FROM crushes WHERE my_username = ? AND target_username = ?', (target_username, my_username))
        match = cursor.fetchone()
        user_states.pop(user_id, None)
        if match:
            match_user_id = match[0]
            kb_me = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔓 Узнать кто это (89 ₽)', callback_data=f'crushpay_{match_user_id}')]])
            kb_them = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔓 Узнать кто это (89 ₽)', callback_data=f'crushpay_{user_id}')]])
            await message.answer('🔥 <b>У ТЕБЯ ВЗАИМНАЯ СИМПАТИЯ!</b>\n\nОдин из людей из твоего списка тоже отметил тебя!\nХочешь узнать, кто именно это был?', parse_mode='HTML', reply_markup=kb_me)
            try:
                await bot.send_message(match_user_id, '🔥 <b>У ТЕБЯ ВЗАИМНАЯ СИМПАТИЯ!</b>\n\nЧеловек, которого ты указал в Crush-тесте, тоже отметил тебя!\nХочешь узнать, кто именно?', parse_mode='HTML', reply_markup=kb_them)
            except Exception:
                pass
        else:
            await message.answer(f'✅ Сохранено! Мы тайно записали симпатию к <b>@{html.escape(target_username)}</b>.\nКак только этот человек тоже введёт твой ник — вы оба получите уведомление!', parse_mode='HTML')
        return
    target_id = state.get('target_id')
    if not target_id:
        user_states.pop(user_id, None)
        return
    pending_messages[user_id] = {'message': message, 'target_id': target_id}
    user_states.pop(user_id, None)
    if is_pair_protected(user_id, target_id):
        msg_db_id = save_message_to_db(message.from_user, target_id, is_protected=1)
        await deliver_to_target(message, target_id, msg_db_id)
        pending_messages.pop(user_id, None)
        await message.answer('✅ <b>Сообщение отправлено анонимно!</b>\n🛡 <i>Действует твоя активная защита диалога — собеседник не сможет тебя раскрыть.</i>', parse_mode='HTML')
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🚀 Отправить бесплатно', callback_data='send_free')],
        [InlineKeyboardButton(text='🛡 Защитить от узнавания (119 ₽)', callback_data='send_protect')]
    ])
    await message.answer('💌 <b>Сообщение готово к отправке!</b>\n\nВыбери способ отправки:\n• <b>Бесплатно</b> — получатель сможет купить подсказку или полное раскрытие.\n• <b>С защитой (119 ₽)</b> — полная конфиденциальность на весь диалог (никто не сможет узнать, кто ты).', parse_mode='HTML', reply_markup=kb)

@dp.callback_query(F.data.startswith('reply_'))
async def process_reply_btn(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    cursor.execute('SELECT sender_id FROM messages WHERE id = ?', (msg_db_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer('Сообщение не найдено.', show_alert=True)
        return
    user_states[callback.from_user.id] = {'mode': 'anon_msg', 'target_id': row[0]}
    await callback.message.answer('↩️ <b>Напиши свой анонимный ответ:</b>\n\nОтправь текст, фото, голосовое или стикер — собеседник получит его анонимно.', parse_mode='HTML')
    await callback.answer()

@dp.callback_query(F.data == 'send_free')
async def process_free_send(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = pending_messages.pop(user_id, None)
    if not data:
        await callback.answer('Сообщение устарело, напиши заново.', show_alert=True)
        return
    msg = data['message']
    target_id = data['target_id']
    msg_db_id = save_message_to_db(msg.from_user, target_id, is_protected=0)
    await deliver_to_target(msg, target_id, msg_db_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✍️ Написать ещё', url=f'https://t.me/{(await bot.get_me()).username}?start={target_id}')]])
    await callback.message.edit_text('✅ <b>Сообщение успешно отправлено анонимно!</b>', parse_mode='HTML', reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == 'send_protect')
async def process_protect_send(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in pending_messages:
        await callback.answer('Сообщение устарело, напиши заново.', show_alert=True)
        return
    await bot.send_invoice(
        chat_id=user_id,
        title='Защита от узнавания (весь диалог)',
        description='Полная конфиденциальность: получатель не сможет узнать твоё имя или @username ни в этом, ни в следующих сообщениях диалога.',
        payload=f'payprotect_{user_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Защита диалога', amount=11900)]
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('urgentprotect_'))
async def process_urgent_protect(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    cursor.execute('SELECT sender_id, target_id, is_protected FROM messages WHERE id = ?', (msg_db_id,))
    row = cursor.fetchone()
    if row and (row[2] == 1 or is_pair_protected(row[0], row[1])):
        await callback.answer('🛡 Защита уже активирована для этого диалога!', show_alert=True)
        return
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='Экстренная защита от узнавания',
        description='Мгновенная блокировка раскрытия: получатель получит отказ при попытке узнать кто ты.',
        payload=f'payurgent_{msg_db_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Экстренная защита', amount=11900)]
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('hint_'))
async def process_hint_request(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    cursor.execute('SELECT sender_id, target_id, is_protected FROM messages WHERE id = ?', (msg_db_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer('Сообщение не найдено.', show_alert=True)
        return
    sender_id, target_id, is_prot = row
    if is_prot == 1 or is_pair_protected(sender_id, target_id):
        await callback.message.answer('😶‍🌫️ Извините, сообщение оплачено и считается конфиденциальным 😶‍🌫️')
        await callback.answer()
        return
    if is_msg_unlocked(callback.from_user.id, msg_db_id, 'hint'):
        await callback.answer('Ты уже открывал подсказку к этому сообщению!', show_alert=True)
        return
    try:
        kb_alert = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🛡 Экстренно защитить (119 ₽)', callback_data=f'urgentprotect_{msg_db_id}')]])
        await bot.send_message(sender_id, '⚠️ <b>ТРЕВОГА!</b> Получатель твоего анонимного сообщения прямо сейчас собирается купить <b>подсказку о тебе</b>!\n\nУспей активировать защиту, пока он не оплатил:', parse_mode='HTML', reply_markup=kb_alert)
    except Exception:
        pass
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='Подсказка об отправителе',
        description='Первая буква имени, наличие @username, язык и приблизительное время активности отправителя.',
        payload=f'payhint_{msg_db_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Подсказка', amount=8900)]
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('reveal_'))
async def process_reveal_request(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])
    cursor.execute('SELECT sender_id, target_id, is_protected FROM messages WHERE id = ?', (msg_db_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer('Сообщение не найдено.', show_alert=True)
        return
    sender_id, target_id, is_prot = row
    if is_prot == 1 or is_pair_protected(sender_id, target_id):
        await callback.message.answer('😶‍🌫️ Извините, сообщение оплачено и считается конфиденциальным 😶‍🌫️')
        await callback.answer()
        return
    if is_msg_unlocked(callback.from_user.id, msg_db_id, 'reveal'):
        await callback.answer('Ты уже раскрывал автора этого сообщения!', show_alert=True)
        return
    try:
        kb_alert = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🛡 Экстренно защитить (119 ₽)', callback_data=f'urgentprotect_{msg_db_id}')]])
        await bot.send_message(sender_id, '🚨 <b>СРОЧНО!</b> Получатель твоего анонимного сообщения нажал <b>«Узнать точно»</b> и переходит к оплате!\n\nЗащити диалог прямо сейчас, иначе он увидит твой профиль:', parse_mode='HTML', reply_markup=kb_alert)
    except Exception:
        pass
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='Полное раскрытие отправителя',
        description='Узнай точное имя, @username и прямую ссылку на профиль отправителя сообщения.',
        payload=f'payreveal_{msg_db_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Раскрытие отправителя', amount=9900)]
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('crushpay_'))
async def process_crush_pay(callback: types.CallbackQuery):
    match_user_id = int(callback.data.split('_')[1])
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='Открыть взаимную симпатию',
        description='Узнай, кто из твоих симпатий ответил тебе взаимностью!',
        payload=f'paycrush_{match_user_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Взаимная симпатия', amount=8900)]
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    payload = pre_checkout_query.invoice_payload
    user_id = pre_checkout_query.from_user.id
    if payload.startswith('payhint_') or payload.startswith('payreveal_'):
        kind = 'hint' if payload.startswith('payhint_') else 'reveal'
        msg_db_id = int(payload.split('_')[1])
        cursor.execute('SELECT sender_id, target_id, is_protected FROM messages WHERE id = ?', (msg_db_id,))
        row = cursor.fetchone()
        if not row or row[2] == 1 or is_pair_protected(row[0], row[1]):
            await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message='Отправитель только что активировал защиту конфиденциальности! Оплата отменена, средства не списаны.')
            return
        if is_msg_unlocked(user_id, msg_db_id, kind):
            await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message='Вы уже оплатили доступ к этому сообщению! Повторное списание отменено.')
            return
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    user_id = message.from_user.id
    try:
        if payload.startswith('paypro_'):
            plan = payload.split('_')[1]
            days_map = {'1m': (30, 199), '6m': (180, 499), '1y': (365, 799)}
            days, price = days_map.get(plan, (30, 199))
            pro_until, _, _, _ = get_sub_status(user_id)
            base_time = max(pro_until, int(time.time()))
            new_until = base_time + days * 86400
            cursor.execute('UPDATE users SET pro_until = ?, trial_used = 1 WHERE user_id = ?', (new_until, user_id))
            cursor.execute('INSERT INTO payments (user_id, pay_type, amount) VALUES (?, ?, ?)', (user_id, f'pro_{plan}', price))
            conn.commit()
            dt = time.strftime('%d.%m.%Y %H:%M', time.localtime(new_until))
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='💬 Открыть чат-рулетку 1 на 1', web_app=WebAppInfo(url=WEBAPP_URL))]])
            await message.answer(f'👑 <b>Подписка PRO успешно активирована!</b>\n\nБезлимит в мини-приложении действует до: <b>{dt}</b>', parse_mode='HTML', reply_markup=kb)
        elif payload.startswith('payprotect_'):
            sender_id = int(payload.split('_')[1])
            data = pending_messages.pop(sender_id, None)
            cursor.execute('INSERT INTO payments (user_id, pay_type, amount) VALUES (?, ?, ?)', (user_id, 'protect', 119))
            conn.commit()
            if data:
                msg = data['message']
                target_id = data['target_id']
                set_pair_protected(sender_id, target_id)
                msg_db_id = save_message_to_db(msg.from_user, target_id, is_protected=1)
                await deliver_to_target(msg, target_id, msg_db_id)
                await message.answer('🛡 <b>Оплата прошла! Сообщение отправлено с полной защитой диалога.</b>\nТеперь получатель не сможет раскрыть тебя ни в этом, ни в следующих сообщениях.', parse_mode='HTML')
            else:
                await message.answer('🛡 Оплата прошла, но текст сообщения не найден в кэше. Напиши сообщение заново — оно отправится с защитой.')
        elif payload.startswith('payurgent_'):
            msg_db_id = int(payload.split('_')[1])
            cursor.execute('SELECT sender_id, target_id FROM messages WHERE id = ?', (msg_db_id,))
            row = cursor.fetchone()
            if row:
                set_pair_protected(row[0], row[1])
            cursor.execute('UPDATE messages SET is_protected = 1 WHERE id = ?', (msg_db_id,))
            cursor.execute('INSERT INTO payments (user_id, pay_type, amount) VALUES (?, ?, ?)', (user_id, 'urgent_protect', 119))
            conn.commit()
            await message.answer('🛡 <b>Экстренная защита активирована!</b>\nВесь ваш диалог с получателем теперь полностью конфиденциален.', parse_mode='HTML')
        elif payload.startswith('payhint_'):
            msg_db_id = int(payload.split('_')[1])
            cursor.execute('SELECT sender_id, sender_username, sender_name, sender_lang, is_protected, created_at FROM messages WHERE id = ?', (msg_db_id,))
            row = cursor.fetchone()
            if not row:
                return
            sender_id, s_user, s_name, s_lang, is_prot, created_at = row
            if is_prot == 1 or is_pair_protected(sender_id, user_id):
                await message.answer('😶‍🌫️ Извините, сообщение оплачено и считается конфиденциальным 😶‍🌫️')
                return
            mark_msg_unlocked(user_id, msg_db_id, 'hint')
            cursor.execute('INSERT INTO payments (user_id, pay_type, amount) VALUES (?, ?, ?)', (user_id, 'hint', 89))
            conn.commit()
            first_letter = html.escape(s_name[0].upper()) if s_name else '?'
            has_user = 'Есть (@username установлен)' if s_user != 'скрыт' else 'Скрыт (нет @username)'
            await message.answer(
                f'🔍 <b>Подсказка об отправителе:</b>\n\n• Первая буква имени: <b>{first_letter}</b>\n• Статус ника: <b>{has_user}</b>\n• Язык Telegram: <b>{html.escape(s_lang)}</b>\n• Отправлено: <b>{html.escape(str(created_at))} UTC</b>',
                parse_mode='HTML'
            )
        elif payload.startswith('payreveal_'):
            msg_db_id = int(payload.split('_')[1])
            cursor.execute('SELECT sender_id, sender_username, sender_name, is_protected FROM messages WHERE id = ?', (msg_db_id,))
            row = cursor.fetchone()
            if not row:
                return
            sender_id, s_user, s_name, is_prot = row
            if is_prot == 1 or is_pair_protected(sender_id, user_id):
                await message.answer('😶‍🌫️ Извините, сообщение оплачено и считается конфиденциальным 😶‍🌫️')
                return
            mark_msg_unlocked(user_id, msg_db_id, 'reveal')
            cursor.execute('INSERT INTO payments (user_id, pay_type, amount) VALUES (?, ?, ?)', (user_id, 'reveal', 99))
            conn.commit()
            safe_name = html.escape(s_name)
            await message.answer(
                f'🕵️‍♂️ <b>Отправитель раскрыт!</b>\n\n• Имя: <a href="tg://user?id={sender_id}">{safe_name}</a>\n• Username: <b>{html.escape(s_user)}</b>\n• ID: <code>{sender_id}</code>',
                parse_mode='HTML'
            )
        elif payload.startswith('paycrush_'):
            match_user_id = int(payload.split('_')[1])
            cursor.execute('INSERT INTO payments (user_id, pay_type, amount) VALUES (?, ?, ?)', (user_id, 'crush', 89))
            conn.commit()
            await message.answer(
                f'💘 <b>Твоя взаимная симпатия раскрыта!</b>\n\n👉 <a href="tg://user?id={match_user_id}">Открыть профиль взаимной симпатии</a>',
                parse_mode='HTML'
            )
    except Exception as e:
        logging.error(f'Payment error: {e}')

@dp.message(Command('admin'))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM messages')
    total_msgs = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM users WHERE pro_until > ?', (int(time.time()),))
    active_pro = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM users WHERE trial_used = 1')
    trials_used = cursor.fetchone()[0]
    cursor.execute('SELECT COALESCE(SUM(amount), 0) FROM payments')
    total_rev = cursor.fetchone()[0]
    cursor.execute('SELECT pay_type, COUNT(*), COALESCE(SUM(amount), 0) FROM payments GROUP BY pay_type')
    breakdown = cursor.fetchall()
    lines = [
        '📊 <b>Панель администратора</b>\n',
        f'👥 Всего пользователей: <b>{total_users}</b>',
        f'💌 Всего анонимных сообщений: <b>{total_msgs}</b>',
        f'🟢 Онлайн в Mini App сейчас: <b>{len(active_ws)}</b>',
        f'👑 Активных PRO-подписок: <b>{active_pro}</b> (триалов выдано: {trials_used})',
        f'💰 Общая выручка: <b>{total_rev} ₽</b>\n',
        '<b>Детализация оплат:</b>'
    ]
    for p_type, cnt, s in breakdown:
        lines.append(f'• {p_type}: {cnt} шт. ({s} ₽)')
    await message.answer('\n'.join(lines), parse_mode='HTML')

async def main():
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f'WebApp server started on port {PORT}')
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
