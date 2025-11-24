import asyncio
from aiogram import Bot, Dispatcher

from app.user import user
from app.admin import admin
from app.inline import router

from config import TOKEN

from app.database.models import async_main





async def main():
    dp = Dispatcher(allow_bot_messages=True)
    dp.include_routers(user, router, admin)
    dp.startup.register(startup)
    bot = Bot(token=TOKEN)

    await dp.start_polling(bot)

async def startup(dispatcher: Dispatcher):
    await async_main()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except:
        print('Exit')