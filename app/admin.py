from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command, Filter

admin = Router()

class Admin(Filter):
    async def __call__(self, message: Message):
        return message.from_user.id in [6848063578]


@admin.message(Admin(), F.photo)
async def get_photo(message: Message):
    file_id =  message.photo[-1].file_id
    file = await message.bot.get_file(file_id)

    file_path = f"ttumb.jpg"
    await message.bot.download_file(file.file_path, destination=file_path)

    await message.answer("✔️ Thumbnail сохранён как ttumb.jpg")


@admin.message(Admin(), F.sticker)
async def get_sticker(message: Message):
    await message.answer(f'ID стикера: {message.sticker.file_id}')