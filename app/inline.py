import re
import io
import os
import json
import aiohttp
import asyncio
import traceback
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultPhoto,ChosenInlineResult,InlineKeyboardButton,
    InputTextMessageContent,InlineKeyboardMarkup,InputMediaAudio,BufferedInputFile, CallbackQuery
)
from aiogram.types.input_file import FSInputFile
from config import bot

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url, get_skysound_mp3
from app.database.requests import rank_tracks_by_similarity


router = Router()
user_tracks ={}
TRACKS_TEMP: dict[str, dict] = {}

FILE_CACHE = {}  # source+url → file_id


# Сохраняем в кэш
def save_file_id(key, file_id):
    FILE_CACHE[key] = file_id


# Получаем из кэша
def get_file_id(key):
    return FILE_CACHE.get(key)


# Скачивание MP3
async def fetch_mp3(t):
    url = t["url"]

    if t["source"] == "SoundCloud":
        mp3 = await get_soundcloud_mp3_url(url)
        if not mp3:
            raise Exception("SC mp3 not found")
        final = mp3

    else:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                html = await r.text()
        links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
        if not links:
            raise Exception("SkySound mp3 not found")
        final = links[0]

    async with aiohttp.ClientSession() as s:
        async with s.get(final) as r:
            if r.status != 200:
                raise Exception("download error")
            return await r.read()


@router.inline_query()
async def inline_search(query: InlineQuery):

    q = query.query.strip()
    if not q:
        return await query.answer([])

    # Тут ты вставляешь свой поиск треков
    # Я ставлю заглушку:
    track = {
        "title": "Song title",
        "artist": "Artist",
        "mp3": "https://example.com/track.mp3",
        "thumb": "https://example.com/thumb.jpg"
    }

    TRACKS_TEMP["track1"] = track

    result = InlineQueryResultPhoto(
        id="track1",
        photo_url=track["thumb"],       # большая фотка
        thumbnail_url=track["thumb"],   # миниатюра в поиске
        caption="⏳ Загружаю аудио…"     # Текст заглушки
    )

    await query.answer([result], cache_time=1)


# -------------------------------------------------------------
# 2. Пользователь выбрал → заменяем фото на АУДИО
# -------------------------------------------------------------
@router.chosen_inline_result()
async def chosen(result: ChosenInlineResult):

    track_id = result.result_id
    inline_id = result.inline_message_id
    track = TRACKS_TEMP.get(track_id)

    if not track:
        return

    # Скачиваем mp3
    async with aiohttp.ClientSession() as s:
        async with s.get(track["mp3"]) as r:
            audio_bytes = await r.read()

    # Заменяем фото на аудио
    await bot.edit_message_media(
        inline_message_id=inline_id,
        media=InputMediaAudio(
            media=audio_bytes,
            title=track["title"],
            performer=track["artist"],
            thumb=track["thumb"]  # работает, Telegram сам скачает
        )
    )

