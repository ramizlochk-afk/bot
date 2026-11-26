import asyncio
import os
import html
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery

# ================= НАСТРОЙКИ =================
TOKEN = "9118480138:AAHoLsyeirweKiuHIMmRtcQwbg6EPUTNZRI"
# Сюда вставь токен из BotFather -> Payments -> ЮKassa (тестовый или боевой)
PROVIDER_TOKEN = "390540012:LIVE:106036"
# =============================================

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Состояния в памяти (кого ждет бот и какое сообщение готовится к отправке)
waiting_recipient = {}  # {sender_id: target_id}
pending_messages = {}   # {msg_db_id: {'sender_id': int, 'target_id': int, 'chat_id': int, 'message_id': int}}

# — НАСТРОЙКА БАЗЫ ДАННЫХ (SQLite) —
os.makedirs('/app/data', exist_ok=True)
conn = sqlite3.connect('/app/data/bot_database.db')
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        sender_username TEXT,
        sender_name TEXT,
        target_id INTEGER,
        is_protected INTEGER DEFAULT 0
    )
''')
conn.commit()

def save_message_to_db(sender: types.User, target_id: int, is_protected: int) -> int:
    username = f"@{sender.username}" if sender.username else "скрыт"
    cursor.execute(
        'INSERT INTO messages (sender_id, sender_username, sender_name, target_id, is_protected) VALUES (?, ?, ?, ?, ?)',
        (sender.id, username, sender.full_name, target_id, is_protected)
    )
    conn.commit()
    return cursor.lastrowid

async def send_personal_link(user_id: int):
    bot_info = await bot.get_me()
    link = f'https://t.me/{bot_info.username}?start={user_id}'
    await bot.send_message(
        user_id,
        f'🔗 Твоя личная ссылка для анонимных вопросов:\n\n{link}\n\n'
        f'Размести её в профиле, сторис или отправь друзьям, чтобы получать тайные признания!'
    )

# — КОМАНДА /start —
@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    args = command.args
    user_id = message.from_user.id
    # Обычный старт: сначала приветствие, потом ссылка
    await message.answer(
        '💞 Добро пожаловать в 🤫 Анонимные вопросы 🤫!\n\n'
        'Здесь ты можешь узнать, что о тебе думают на самом деле, получить тайные признания или задать каверзный вопрос друзьям, оставаясь в тени.'
    )
    await send_personal_link(user_id)
    # Если перешли по чьей-то ссылке
    if args and args.isdigit():
        target_id = int(args)
        if target_id == user_id:
            await message.answer('🤭 Вы перешли по своей же ссылке! Отправьте её друзьям, чтобы получать сообщения.')
            return

        waiting_recipient[user_id] = target_id
        await message.answer(
            '🤫 **Режим анонимного сообщения включён!**\n\n'
            'Напишите текст, отправьте фото, видео, голосовое или кружочек — я передам это получателю.',
            parse_mode='Markdown'
        )


# — КОМАНДА /link ДЛЯ ПОВТОРНОГО ПОЛУЧЕНИЯ ССЫЛКИ —
@dp.message(Command('link'))
async def cmd_link(message: types.Message):
    await send_personal_link(message.from_user.id)

# — ФУНКЦИЯ ДОСТАВКИ СООБЩЕНИЯ ПОЛУЧАТЕЛЮ —
async def deliver_to_target(msg_db_id: int, from_chat_id: int, from_message_id: int, target_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🧑‍💻 Узнать кто написал (99 ₽) 🧑‍💻', callback_data=f'reveal_{msg_db_id}')]
    ])
    try:
        await bot.send_message(target_id, '💌 **Тебе пришло новое анонимное сообщение!**', parse_mode='Markdown')
        await bot.copy_message(
            chat_id=target_id,
            from_chat_id=from_chat_id,
            message_id=from_message_id,
            reply_markup=kb
        )
        return True
    except Exception:
        return False

# — ОБРАБОТКА ВХОДЯЩИХ СООБЩЕНИЙ —
@dp.message(~F.successful_payment)
async def handle_messages(message: types.Message):
    user_id = message.from_user.id

# Если человек сейчас пишет сообщение по чьей-то ссылке
    if user_id in waiting_recipient:
        target_id = waiting_recipient.pop(user_id)

        # Создаем временную запись в словаре до выбора кнопки Да/Нет
        temp_id = f"{user_id}_{message.message_id}"
        pending_messages[temp_id] = {
            'sender': message.from_user,
            'target_id': target_id,
            'chat_id': message.chat.id,
            'message_id': message.message_id
        }

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text='Да (119 ₽)', callback_data=f'protect_yes_{temp_id}'),
                InlineKeyboardButton(text='Нет (Бесплатно)', callback_data=f'protect_no_{temp_id}')
            ]
        ])

        await message.answer(
            '🤫 **Желаете отправить ваше сообщение анонимно?**\n\n'
            'Учтите: при отказе человек может узнать, кто отправил это сообщение, '
            'но это тоже платно, так что может и не узнать 🤫\n\n'
            '👇 *Ваше сообщение:*',
            parse_mode='Markdown'
        )
        # Повторяем сообщение пользователя с кнопками Да / Нет
        await message.copy_to(chat_id=message.chat.id, reply_markup=kb)

    else:
        # Если человек просто написал текст без перехода по ссылке
        await message.answer(
            '🤔 Чтобы отправить анонимное сообщение, нужно перейти по личной ссылке человека.\n\n'
            'А если хочешь получить свою ссылку для друзей — нажми /link'
        )

# — ОБРАБОТКА КНОПОК "ДА" И "НЕТ" (ОТПРАВКА С ЗАЩИТОЙ ИЛИ БЕЗ) —
@dp.callback_query(F.data.startswith('protect_'))
async def process_protection_choice(callback: types.CallbackQuery):
    parts = callback.data.split('_')
    choice = parts[1]  # 'yes' или 'no'
    temp_id = f"{parts[2]}_{parts[3]}"

    data = pending_messages.get(temp_id)
    if not data:
        await callback.answer('Это сообщение уже обработано или устарело.', show_alert=True)
        return

    if choice == 'no':
        # Бесплатная отправка (без защиты)
        pending_messages.pop(temp_id, None)
        msg_db_id = save_message_to_db(data['sender'], data['target_id'], is_protected=0)
        success = await deliver_to_target(msg_db_id, data['chat_id'], data['message_id'], data['target_id'])

        await callback.message.edit_reply_markup(reply_markup=None)
        if success:
            await callback.message.answer('✅ Сообщение отправлено (без защиты от раскрытия)!\n\nПолучить свою ссылку: /link')
        else:
            await callback.message.answer('❌ Не удалось доставить сообщение (получатель заблокировал бота).')
        await callback.answer()

    elif choice == 'yes':
        # Платная отправка с защитой (119 рублей = 11900 копеек)
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title='Защита анонимности (100%)',
            description='Никто не сможет узнать, кто отправил это сообщение, даже за деньги.',
            payload=f'payprotect_{temp_id}',
            provider_token=PROVIDER_TOKEN,
            currency='RUB',
            prices=[LabeledPrice(label='Полная анонимность', amount=11900)]
        )
        await callback.answer()

# — ОБРАБОТКА КНОПКИ "УЗНАТЬ КТО НАПИСАЛ (99 ₽)" —
@dp.callback_query(F.data.startswith('reveal_'))
async def process_reveal_request(callback: types.CallbackQuery):
    msg_db_id = int(callback.data.split('_')[1])

    # Выставляем счёт на 99 рублей (9900 копеек)
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title='Узнать отправителя',
        description='Раскрытие автора анонимного сообщения.',
        payload=f'payreveal_{msg_db_id}',
        provider_token=PROVIDER_TOKEN,
        currency='RUB',
        prices=[LabeledPrice(label='Раскрытие автора', amount=9900)]
    )
    await callback.answer()

# — ПОДТВЕРЖДЕНИЕ ПЕРЕД ОПЛАТОЙ (ОБЯЗАТЕЛЬНО ДЛЯ TELEGRAM PAYMENTS) —
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


# — ДЕЙСТВИЯ ПОСЛЕ УСПЕШНОЙ ОПЛАТЫ —
@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
try:
    payload = message.successful_payment.invoice_payload
    
        # 1. Оплачена защита сообщения (119 ₽)
        if payload.startswith('payprotect_'):
            temp_id = payload.replace('payprotect_', '')
            data = pending_messages.pop(temp_id, None)
    
            if not data:
                await message.answer('✅ Оплата прошла, но сообщение уже было отправлено ранее.')
                return
    
            msg_db_id = save_message_to_db(data['sender'], data['target_id'], is_protected=1)
            success = await deliver_to_target(msg_db_id, data['chat_id'], data['message_id'], data['target_id'])
    
            if success:
                await message.answer(
                    '✅ **Оплата прошла успешно!**\n\n'
                    'Ваше сообщение отправлено под полной защитой. Получатель не сможет узнать, кто вы.\n\n'
                    'Получить свою ссылку: /link',
                    parse_mode='Markdown'
                )
            else:
                await message.answer('❌ Не удалось доставить сообщение (получатель заблокировал бота).')
    
        # 2. Оплачено раскрытие отправителя (99 ₽)
        elif payload.startswith('payreveal_'):
            msg_db_id = int(payload.replace('payreveal_', ''))
    
            cursor.execute('SELECT sender_id, sender_username, sender_name, is_protected FROM messages WHERE id = ?', (msg_db_id,))
            row = cursor.fetchone()
    
            if not row:
                await message.answer('❌ Информация об этом сообщении не найдена (возможно, бот перезагружался).')
                return
    
            sender_id, sender_username, sender_name, is_protected = row
    
            # Проверяем, оплатил ли отправитель защиту за 119 ₽
            if is_protected == 1:
                await message.answer('😶‍🌫️ Извините, сообщение оплачено и считается конфиденциальным 😶‍🌫️')
            else:
                if sender_username != "скрыт":
                    await message.answer(f'🕵️‍♂️ Это сообщение отправил(а): {sender_username}')
                else:
                    safe_name = html.escape(sender_name)
                    await message.answer(
                        f'🕵️‍♂️ У отправителя скрыт @username, но вот его профиль:\n'
                        f'Имя: <a href="tg://user?id={sender_id}">{safe_name}</a>',
                        parse_mode='HTML'
                    )
except Exception as e:
    await message.answer(f'⚠️ Техническая ошибка при выдаче: {e}')
            
async def main():
    print("Бот успешно запущен и работает!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
