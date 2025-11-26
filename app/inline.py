import re
import io
import os
import tempfile
import aiohttp
import asyncio
import traceback
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent,InlineKeyboardMarkup,InlineKeyboardButton, CallbackQuery
)
from aiogram.types.input_file import FSInputFile
from config import bot

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url, get_skysound_mp3

router = Router()

@router.inline_query()
async def inline_search(q: InlineQuery):
    btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Скачать 🎵", callback_data=f"dl_{q.query}")]
    ])

    result = InlineQueryResultArticle(
        id="test1",
        title="Нажми кнопку",
        input_message_content=InputTextMessageContent(message_text="🎧 Выберите действие"),
        reply_markup=btn
    )

    await q.answer([result], cache_time=0)

@router.callback_query(F.data.startswith("dl_"))
async def on_dl(cb: CallbackQuery):
    query = cb.data[3:]

    await cb.answer("Начинаю загрузку...")
    await cb.message.answer(f"Вы запросили загрузку: {query}")

