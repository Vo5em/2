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
async def inline_search(q: InlineQuery):
    query = q.query.strip()
    if not query:
        return await q.answer([])

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

        #
        # ВАЖНО: заранее сохраняем mp3 URL
        #
        if t["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(t["url"])
        else:
            # в skysound url == страница → mp3 будет искаться позже через fetch_mp3
            mp3_url = t["url"]

        TRACKS_TEMP[tid] = {
            "artist": t["artist"],
            "title": t["title"],
            "thumb": t["thumb"],
            "source": t["source"],
            "url": t["url"],     # нужен fetch_mp3
            "mp3": mp3_url       # нужен chosen_inline_result
        }

        results.append(
            InlineQueryResultArticle(
                id=tid,
                title=f"{t['artist']} — {t['title']}",
                description=t["source"],
                thumb_url=t["thumb"],
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⏳ Загрузка…", callback_data="stub")]
                    ]
                ),
                input_message_content=InputTextMessageContent(
                    message_text="⏳ Загружаю трек…"
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
    if not track:
        print("❌ track не найден")
        return

    # 1) Обновляем сообщение
    await bot.edit_message_text(
        inline_message_id=inline_id,
        text="🔄 Загружаю аудио…"
    )

    # 2) Скачиваем MP3
    try:
        audio_bytes = await fetch_mp3(track)
    except Exception as e:
        print("mp3 error:", e)
        await bot.edit_message_text(
            inline_message_id=inline_id,
            text="❌ Ошибка загрузки MP3"
        )
        return

    # 3) Качаем обложку
    thumb_bytes = None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(track["thumb"]) as r:
                if r.status == 200:
                    thumb_bytes = await r.read()
    except:
        pass

    # 4) Заменяем сообщение на аудио
    try:
        await bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaAudio(
                media=BufferedInputFile(
                    audio_bytes,
                    filename=f"{track['artist']} - {track['title']}.mp3"
                ),
                title=track["title"],
                performer=track["artist"],
                thumb=(
                    BufferedInputFile(thumb_bytes, "cover.jpg")
                    if thumb_bytes else None
                )
            )
        )
    except Exception as e:
        print("edit_message_media error:", e)
        await bot.edit_message_text(
            inline_message_id=inline_id,
            text="❌ Ошибка при отправке аудио"
        )
        return

