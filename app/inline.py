import aiohttp
from aiogram import Router
from aiogram.types import (
    InlineQuery, InlineQueryResultDocument
)

from app.database.requests import (
    search_soundcloud, search_skysound,
    get_soundcloud_mp3_url,
    rank_tracks_by_similarity
)

router = Router()

# tid → track data
TRACKS_TEMP: dict[str, dict] = {}


# ==========================
# INLINE SEARCH
# ==========================
@router.inline_query()
async def inline_search(q: InlineQuery):
    query = q.query.strip()
    if not query:
        return await q.answer([])

    # 1. ищем треки
    tracks = []
    tracks += await search_skysound(query)
    tracks += await search_soundcloud(query)
    tracks = rank_tracks_by_similarity(query, tracks)

    results = []

    for i, t in enumerate(tracks[:20]):
        tid = f"{q.from_user.id}_{i}"

        # заранее получаем прямой mp3 URL
        if t["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(t["url"])
        else:
            mp3_url = t["url"]    # у тебя уже готовый MP3

        # сохраняем (если вдруг пригодится)
        TRACKS_TEMP[tid] = {
            "artist": t["artist"],
            "title": t["title"],
            "thumb": t["thumb"],
            "mp3": mp3_url
        }

        # 2. Telegram сам скачает этот mp3 и отправит как аудио
        results.append(
            InlineQueryResultDocument(
                id=tid,
                title=f"{t['artist']} — {t['title']}",
                description="🎵 " + t["artist"],
                thumb_url=t["thumb"],
                document_url=mp3_url,
                mime_type="audio/mpeg",
            )
        )

    await q.answer(results, cache_time=1)

