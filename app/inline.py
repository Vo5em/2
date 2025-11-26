import io
import re
import aiohttp
import asyncio
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent,BufferedInputFile, ChosenInlineResult
)
from aiogram.types.input_file import FSInputFile
from config import bot

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url, get_skysound_mp3

router = Router()

# временный кэш
TRACKS_TEMP = {}   # result_id → full track dict
MP3_CACHE = {}

async def get_mp3(track: dict) -> str | None:
    """
    Универсальная функция: пытается достать mp3 из track.
    """

    url = track.get("url")
    if not url:
        return None

    # сперва смотрим кэш
    if url in MP3_CACHE:
        return MP3_CACHE[url]

    # источник SoundCloud
    if track.get("source") == "soundcloud":
        mp3 = await get_soundcloud_mp3_url(url)
        if mp3:
            MP3_CACHE[url] = mp3
            return mp3

    # источник skysound
    if track.get("source") == "skysound":
        mp3 = await get_skysound_mp3(url)
        if mp3:
            MP3_CACHE[url] = mp3
            return mp3

    return None


@router.inline_query()
async def inline_search(q: InlineQuery):
    text = q.query.strip()
    if not text:
        return await q.answer([], cache_time=0)

    tracks = []
    tracks += await search_soundcloud(text)
    tracks += await search_skysound(text)

    results = []

    for i, t in enumerate(tracks[:30]):
        uid = f"trk_{i}"

        # сохраняем полный объект
        TRACKS_TEMP[uid] = t

        results.append(
            InlineQueryResultArticle(
                id=uid,
                title=f"{t['artist']} — {t['title']}",
                description=t.get("duration", ""),
                thumbnail_url=t.get("thumb"),   # обложка в инлайн-поиске
                input_message_content=InputTextMessageContent(
                    message_text=f"🎵 {t['artist']} — {t['title']}"
                )
            )
        )

    await q.answer(results, cache_time=0)


@router.chosen_inline_result()
async def on_choose(res: ChosenInlineResult):

    tid = res.result_id
    track = TRACKS_TEMP.get(tid)

    if not track:
        return

    chat_id = res.from_user.id

    # -------- mp3 URL --------
    mp3_url = track.get("mp3")
    if not mp3_url:
        mp3_url = await get_mp3(track)

    if not mp3_url:
        return await res.bot.send_message(chat_id, "Ошибка: не найден mp3 😢")

    # -------- скачивание mp3 --------
    async with aiohttp.ClientSession() as sess:
        async with sess.get(mp3_url) as r:
            mp3_bytes = await r.read()

    audio = BufferedInputFile(mp3_bytes, filename="track.mp3")

    # -------- скачивание обложки --------
    thumb = None
    if track.get("thumb"):
        async with aiohttp.ClientSession() as sess:
            async with sess.get(track["thumb"]) as r:
                thumb_bytes = await r.read()
                thumb = BufferedInputFile(thumb_bytes, filename="cover.jpg")

    # -------- отправка аудио --------
    await res.bot.send_audio(
        chat_id=chat_id,
        audio=audio,
        performer=track["artist"],
        title=track["title"],
        thumbnail=thumb
    )

