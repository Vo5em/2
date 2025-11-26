import io
import re
import aiohttp
import asyncio
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent,BufferedInputFile,InputMediaAudio, ChosenInlineResult
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

    await q.answer(results, cache_time=2)


@router.chosen_inline_result()
async def on_choose(res: ChosenInlineResult):
    tid = res.result_id
    track = TRACKS_TEMP.get(tid)

    if not track:
        return

    inline_id = res.inline_message_id
    if not inline_id:
        # Такое возможно если запрос был в ПМ с ботом, но тогда chat_id есть
        return

    # -------- получаем mp3 --------
    mp3_url = track.get("mp3") or await get_mp3(track)
    if not mp3_url:
        return

    # скачиваем mp3
    async with aiohttp.ClientSession() as sess:
        async with sess.get(mp3_url) as r:
            audio_bytes = await r.read()

    audio = BufferedInputFile(audio_bytes, filename="track.mp3")

    # скачиваем обложку
    thumb = None
    if track.get("thumb"):
        async with aiohttp.ClientSession() as sess:
            async with sess.get(track["thumb"]) as r:
                thumb_bytes = await r.read()
                thumb = BufferedInputFile(thumb_bytes, filename="cover.jpg")

    # -------- ЗАМЕНЯЕМ заглушку на аудио --------
    await res.bot.edit_message_media(
        inline_message_id=inline_id,
        media=InputMediaAudio(
            media=audio,
            title=track["title"],
            performer=track["artist"],
            thumbnail=thumb
        )
    )

