import asyncio
import os 
import sys
#parents
from maxapi import Bot,Dispatcher,Command

#import of the token from powershells's variable
token = os.environ.get("MAX_BOT_TOKEN")

#objects of the classes
bot = Bot(token)
dispatcher = Dispatcher()

#command introducing
@dispatcher.message_created(Command("start"))
async def start_event(event):
    await event.message.answer("Привет! Это «Место встречи» — сервис для поиска школьных мероприятий, проектов и единомышленников. Пока мы готовим первую версию!")

#main func for futher works with data-bases
async def main():
    print("Бот успешно запущен и слушает сервер через Long Polling...")
    await dispatcher.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
