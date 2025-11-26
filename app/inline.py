import re
import io
import os
import json
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

    # Ищем треки
    tracks = []
    tracks += await search_skysound(query)
    tracks += await search_soundcloud(query)

    if not tracks:
        return await q.answer([
            InlineQueryResultArticle(
                id="notfound",
                title="Ничего не найдено",
                input_message_content=InputTextMessageContent(
                    f"По запросу «{query}» ничего не найдено"
                )
            )
        ])

    tracks = rank_tracks_by_similarity(query, tracks)

    results = []
    for i, t in enumerate(tracks[:20]):
        tid = f"{q.from_user.id}_{i}"
        TRACKS_TEMP[tid] = t

        results.append(
            InlineQueryResultArticle(
                id=tid,
                title=f"{t['artist']} — {t['title']}",
                description=t["source"],
                thumb_url=t["thumb"],
                input_message_content=InputTextMessageContent(
                    message_text="⏳ Загружаю трек…"
                )
            )
        )

    await q.answer(results, cache_time=1)


@router.chosen_inline_result()
async def chosen(res: ChosenInlineResult):
    # Полный дамп объекта
    try:
        print("=== RAW ChosenInlineResult ===")
        print(json.dumps(res.model_dump(), indent=2, ensure_ascii=False))
        print("=== END RAW ===")
    except Exception as e:
        print("Failed to dump chosen:", e, repr(res))

    tid = res.result_id

    if tid not in TRACKS_TEMP:
        print("⚠ TRACKS_TEMP: ключ не найден:", tid)
        return

    track = TRACKS_TEMP[tid]
    inline_id = res.inline_message_id

    if not inline_id:
        print("❌ inline_message_id отсутствует")
        print("  user:", res.from_user.id)
        print("  result_id:", res.result_id)
        print("  query:", res.query)
        print("  ВАЖНО: Telegram не создаёт inline_message_id для Article/input_message_content")
        return

    print("✅ inline_message_id:", inline_id)

