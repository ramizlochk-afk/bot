import asyncio, sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command, CommandObject

ADMIN_ID = 8263206498
TOKEN = "9118480138:AAHoLsyeirweKiuHIMmRtcQwbg6EPUTNZRI"

bot = Bot(token=TOKEN)
dp = Dispatcher()
recipients = {}
known_users = set()

conn = sqlite3.connect("users.db")
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
conn.commit()

def add_user(user_id):
cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
conn.commit()

@dp.message(Command("stat"))
async def cmd_stat(message: types.Message):
if message.from_user.id != ADMIN_ID:
return
cur.execute("SELECT COUNT(*) FROM users")
count = cur.fetchone()[0]
await message.answer(f"📊 Статистика бота:\nВсего пользователей: {count}")

@dp.message(Command("send"))
async def cmd_send(message: types.Message, command: CommandObject):
if message.from_user.id != ADMIN_ID:
return
if not command.args:
await message.answer("Напишите текст после /send, например: /send Привет всем!")
return
cur.execute("SELECT user_id FROM users")
users = cur.fetchall()
ok = 0
for (uid,) in users:
try:
await bot.send_message(uid, command.args)
ok += 1
await asyncio.sleep(0.05)
except Exception:
pass
await message.answer(f"✅ Рассылка завершена. Доставлено: {ok}")

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
uid = message.from_user.id
add_user(uid)
args = command.args
if args and args.isdigit():
target_id = int(args)
if target_id == uid:
await message.answer("Вы перешли по своей ссылке! Отправьте ее друзьям.")
return
recipients[uid] = target_id
await message.answer("Напишите сообщение, и я анонимно перешлю его получателю.")
else:
bot_info = await bot.get_me()
link = f"https://t.me{bot_info.username}?start={uid}"
await message.answer(f"Привет! Вот твоя личная ссылка:\n\n{link}")

@dp.message()
async def forward_message(message: types.Message):
uid = message.from_user.id
add_user(uid)
if uid in recipients:
target_id = recipients.pop(uid)
try:
await bot.send_message(target_id, "📩 Новое анонимное сообщение:")
await message.copy_to(target_id)
if uid not in known_users:
known_users.add(uid)
bot_info = await bot.get_me()
link = f"https://t.me{bot_info.username}?start={uid}"
await message.answer(f"✅ Сообщение доставлено!\n\nТвоя личная ссылка для получения вопросов:\n{link}")
else:
await message.answer("✅ Сообщение доставлено!")
except Exception:
await message.answer("❌ Не удалось доставить сообщение.")
else:
bot_info = await bot.get_me()
link = f"https://t.me{bot_info.username}?start={uid}"
await message.answer(f"Твоя ссылка: {link}")

async def main():
print("Бот успешно запущен на сервере!")
await dp.start_polling(bot)

if __name__ == "__main__":
asyncio.run(main())
