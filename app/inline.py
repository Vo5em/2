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


async def get_mp3(track):
    url = track.get("url")
    if not url:
        return None

    if url in MP3_CACHE:
        return MP3_CACHE[url]

    if track.get("source") == "soundcloud":
        mp3 = await get_soundcloud_mp3_url(url)
        if mp3:
            MP3_CACHE[url] = mp3
            return mp3

    if track.get("source") == "skysound":
        mp3 = await get_skysound_mp3(url)
        if mp3:
            MP3_CACHE[url] = mp3
            return mp3

    return None


# ----------------------- INLINE SEARCH -----------------------
@router.inline_query()
async def inline_search(q: InlineQuery):

    text = q.query.strip()
    if not text:
        return await q.answer([], cache_time=1)

    tracks = []
    tracks += await search_soundcloud(text)
    tracks += await search_skysound(text)

    results = []

    for i, t in enumerate(tracks[:30]):
        uid = f"trk_{i}"
        TRACKS_TEMP[uid] = t

        results.append(
            InlineQueryResultArticle(
                id=uid,
                title=f"{t['artist']} — {t['title']}",
                description=t.get("duration", ""),
                thumbnail_url=t.get("thumb"),
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "⏳ Загружаю аудио...\n\n"
                        f"🎵 {t['artist']} — {t['title']}"
                    )
                )
            )
        )

    await q.answer(results, cache_time=2)


# ----------------------- WHEN USER SELECTS A TRACK -----------------------
@router.chosen_inline_result()
async def on_choose(res: ChosenInlineResult):

    tid = res.result_id
    track = TRACKS_TEMP.get(tid)

    if not track:
        return

    inline_id = res.inline_message_id
    if not inline_id:
        return  # если пользователь выбрал в ЛС бота — заменить нечего

    # ---- получаем mp3 ----
    mp3_url = track.get("mp3") or await get_mp3(track)
    if not mp3_url:
        return

    # ---- скачиваем mp3 ----
    async with aiohttp.ClientSession() as sess:
        async with sess.get(mp3_url) as r:
            audio_bytes = await r.read()

    audio = BufferedInputFile(audio_bytes, filename="track.mp3")

    # ---- скачиваем обложку ----
    thumb = None
    if track.get("thumb"):
        async with aiohttp.ClientSession() as sess:
            async with sess.get(track["thumb"]) as r:
                thumb_bytes = await r.read()
                thumb = BufferedInputFile(thumb_bytes, filename="cover.jpg")

    # ---- ЗАМЕНЯЕМ заглушку на аудио ----
    try:
        await res.bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaAudio(
                media=audio,
                title=track["title"],
                performer=track["artist"],
                thumbnail=thumb
            )
        )
    except Exception as e:
        print("edit_message_media error:", e)

