import re
import io
import os
import tempfile
import aiohttp
import asyncio
import traceback
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,ChosenInlineResult,
    InputTextMessageContent,InlineKeyboardMarkup,InputMediaAudio,BufferedInputFile, CallbackQuery
)
from aiogram.types.input_file import FSInputFile
from config import bot

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url, get_skysound_mp3
from app.database.requests import rank_tracks_by_similarity


router = Router()
user_tracks ={}
TRACKS_TEMP: dict[str, dict] = {}

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

    tracks = []
    tracks += await search_skysound(query)
    tracks += await search_soundcloud(query)

    if not tracks:
        return await q.answer([
            InlineQueryResultArticle(
                id="notfound",
                title="Ничего не найдено",
                input_message_content=InputTextMessageContent(message_text=
                    f"По запросу «{query}» ничего не найдено"
                )
            )
        ])

    tracks = rank_tracks_by_similarity(query, tracks)

    results = []

    for i, t in enumerate(tracks[:20]):
        title = f"{t['artist']} — {t['title']}"
        tid = f"{q.from_user.id}_{i}"  # УНИКАЛЬНЫЙ ID inline результата

        # сохраняем инфу для chosen_inline_result
        TRACKS_TEMP[tid] = t

        results.append(
            InlineQueryResultArticle(
                id=tid,
                title=title,
                description=t["source"],
                input_message_content=InputTextMessageContent(message_text=
                    f"Загружаю трек… {title}"
                )
            )
        )

    await q.answer(results, cache_time=1)

@router.chosen_inline_result()
async def chosen(res: ChosenInlineResult):
    tid = res.result_id
    if tid not in TRACKS_TEMP:
        print("NO TRACK FOUND")
        return

    track = TRACKS_TEMP[tid]

    inline_id = res.inline_message_id
    if not inline_id:
        return

    # грузим mp3
    try:
        mp3_bytes = await fetch_mp3(track)
    except Exception as e:
        print("MP3 error:", e)
        return

    # грузим обложку
    thumb_bytes = None
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(track["thumb"]) as r:
                thumb_bytes = await r.read()
    except:
        pass

    await res.bot.edit_message_media(
        inline_message_id=inline_id,
        media=InputMediaAudio(
            media=BufferedInputFile(mp3_bytes, "track.mp3"),
            title=track["title"],
            performer=track["artist"],
            thumbnail=(
                BufferedInputFile(thumb_bytes, "cover.jpg")
                if thumb_bytes else None
            )
        )
    )

