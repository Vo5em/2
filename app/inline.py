import re
import io
import os
import json
import aiohttp
import asyncio
import traceback
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,ChosenInlineResult,InlineKeyboardButton,
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
async def fetch_mp3(track):
    url = track["url"]

    if track["source"] == "SoundCloud":
        mp3 = await get_soundcloud_mp3_url(url)
        if not mp3:
            raise Exception("SC mp3 not found")
        final = mp3

    else:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                html = await r.text()
        links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
        if not links:
            raise Exception("SkySound mp3 not found")
        final = links[0]

    async with aiohttp.ClientSession() as session:
        async with session.get(final) as r:
            if r.status != 200:
                raise Exception("download error")
            return await r.read()


@router.inline_query()
async def inline_search(q: InlineQuery):
    query = q.query.strip()

    if not query:
        return await q.answer([])

    # Получаем треки
    tracks = []
    tracks += await search_skysound(query)
    tracks += await search_soundcloud(query)

    if not tracks:
        return await q.answer([
            InlineQueryResultArticle(
                id="nf",
                title="Ничего не найдено",
                input_message_content=InputTextMessageContent(message_text="Ничего не найдено")
            )
        ])

    tracks = rank_tracks_by_similarity(query, tracks)
    results = []

    for i, t in enumerate(tracks[:20]):
        tid = f"{q.from_user.id}_{i}"
        TRACKS_TEMP[tid] = t

        # Показываем Article с обложкой
        results.append(
            InlineQueryResultArticle(
                id=tid,
                title=f"{t['artist']} — {t['title']}",
                description=t["source"],
                thumb_url=t["thumb"],  # обложка в inline preview
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⏳ Загрузка…", callback_data="stub")]
                    ]
                ),
                input_message_content=InputTextMessageContent(message_text=
                    "⏳ Загружаю трек…"
                )
            )
        )

    await q.answer(results, cache_time=1)



# ===============================
#       USER CHOSE RESULT
# ===============================
@router.chosen_inline_result()
async def chosen_track(result: ChosenInlineResult):

    inline_id = result.inline_message_id
    if not inline_id:
        print("❌ inline_message_id отсутствует")
        return

    track_id = result.result_id
    track = TRACKS_TEMP.get(track_id)

    # 1) Пишем "Загружаю"
    await bot.edit_message_text(
        inline_message_id=inline_id,
        text="🔄 Загружаю аудио…"
    )

    # 2) Скачиваем MP3
    async with aiohttp.ClientSession() as s:
        async with s.get(track["mp3"], timeout=25) as r:
            audio_bytes = await r.read()

    # 3) Отдаём аудио как media update
    await bot.edit_message_media(
        inline_message_id=inline_id,
        media=InputMediaAudio(
            media=BufferedInputFile(
                audio_bytes,
                filename=f"{track['artist']} - {track['title']}.mp3"
            ),
            title=track["title"],
            performer=track["artist"],
            thumb="ttumb.jpg"
        )
    )

