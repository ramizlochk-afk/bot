import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, CommandObject


TOKEN = '9118480138:AAHoLsyeirweKiuHIMmRtcQwbg6EPUTNZRI'
bot = Bot(token=TOKEN)
dp = Dispatcher()
recipients = {}
known_users = set()

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    args = command.args
    if args and args.isdigit():
        target_id = int(args)
        if target_id == message.from_user.id:
            await message.answer('Вы перешли по своей ссылке! Отправьте ее друзьям.')
            return
        recipients[message.from_user.id] = target_id
        await message.answer('Напишите сообщение, и я анонимно перешлю его получателю.')
    else:
        bot_info = await bot.get_me()
        link = f'https://t.me/{bot_info.username}?start={message.from_user.id}'
        await message.answer(f'Привет! Вот твоя личная ссылка: {link}')

@dp.message()
async def forward_message(message: types.Message):
    user_id = message.from_user.id
    if user_id in recipients:
        target_id = recipients.pop(user_id)
        try:
            await bot.send_message(target_id, 'Новое анонимное сообщение:')
            await message.copy_to(target_id)
            await message.answer('Сообщение доставлено!')
        except Exception:
            await message.answer('Не удалось доставить сообщение.')
    else:
        bot_info = await bot.get_me()
        link = f'https://t.me/{bot_info.username}?start={user_id}'
        await message.answer(f'Твоя ссылка: {link}')

async def main():
    print('Бот успешно запущен и работает!')
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
