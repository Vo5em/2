import re
import aiohttp
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent, InputMediaAudio,
    BufferedInputFile, Message
)
from config import bot

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url
from app.database.requests import rank_tracks_by_similarity

router = Router()

# tid → track-info
TRACKS_TEMP: dict[str, dict] = {}


# ===========================
# СКАЧИВАНИЕ MP3
# ===========================
async def fetch_mp3(t):
    if t["source"] == "SoundCloud":
        mp3_url = await get_soundcloud_mp3_url(t["url"])
    else:
        mp3_url = t["url"]

    async with aiohttp.ClientSession() as s:
        async with s.get(mp3_url) as r:
            return await r.read()


# ===========================
# СКАЧИВАНИЕ ОБЛОЖКИ
# ===========================
async def fetch_thumb(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status == 200:
                    return await r.read()
    except:
        pass
    return None


# ==========================================
# 1. INLINE SEARCH (показываем обложки)
# ==========================================
@router.inline_query()
async def inline_search(q: InlineQuery):
    query = q.query.strip()
    if not query:
        return await q.answer([])

    tracks = []
    tracks += await search_skysound(query)
    tracks += await search_soundcloud(query)
    tracks = rank_tracks_by_similarity(query, tracks)

    results = []

    for i, t in enumerate(tracks[:20]):
        tid = f"{q.from_user.id}_{i}"

        # сохраняем трек в кэш
        TRACKS_TEMP[tid] = t

        results.append(
            InlineQueryResultArticle(
                id=tid,
                title=f"{t['artist']} — {t['title']}",
                description=t["source"],
                thumb_url=t["thumb"],
                input_message_content=InputTextMessageContent(
                    message_text=f"[id:{tid}] ⏳ Загружаю {t['artist']} — {t['title']}…"
                )
            )
        )

    await q.answer(results, cache_time=0)


# ==============================================================
# 2. ПОЛЬЗОВАТЕЛЬ ОТПРАВИЛ INLINE ARTICLE (тут мы получаем chat_id)
# ==============================================================

@router.message()
async def catch_all(msg: Message):
    print("Message in private:", msg.text, msg.via_bot)

