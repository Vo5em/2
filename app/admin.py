from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command, Filter

admin = Router()

class Admin(Filter):
    async def __call__(self, message: Message):
        return message.from_user.id in [6848063578]


@admin.message(Admin(), F.photo)
async def get_photo(message: Message):
    await message.answer(f'ID фотографии: {message.photo[-1].file_id}')


@admin.message(Admin(), F.sticker)
async def get_sticker(message: Message):
    await message.answer(f'ID стикера: {message.sticker.file_id}')