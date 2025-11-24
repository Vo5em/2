import re
import aiohttp
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultAudio,
)
from app.database.requests import (
    search_skysound,
    search_soundcloud,
    rank_tracks_by_similarity,
    get_soundcloud_mp3_url
)

router = Router()

# ===================== INLINE ======================
@router.inline_query()
async def inline_search(query: InlineQuery):
    text = query.query.strip()

    if not text:
        await query.answer([], cache_time=1)
        return

    # --- Ищем треки ---
    tracks = []
    tracks += await search_skysound(text)
    tracks += await search_soundcloud(text)

    if not tracks:
        await query.answer([], cache_time=1)
        return

    # Сортировка по похожести
    tracks = rank_tracks_by_similarity(text, tracks)

    results = []

    # Строим только InlineQueryResultAudio
    for idx, track in enumerate(tracks[:20]):

        # === Получаем прямой MP3 URL заранее ===
        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(track["url"])
        else:
            # html парсинг mp3 ссылки
            async with aiohttp.ClientSession() as session:
                async with session.get(track["url"], timeout=10) as resp:
                    html = await resp.text()
            mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            mp3_url = mp3_links[0] if mp3_links else None

        if not mp3_url:
            continue

        # === InlineQueryResultAudio ===
        results.append(
            InlineQueryResultAudio(
                id=str(idx),
                audio_url=mp3_url,
                title=track["title"],
                performer=track["artist"],
                caption='<a href="https://t.me/eschalon">eschalon</a>, <a href="t.me/eschalonmusicbot">music</a>',
                parse_mode="HTML",
            )
        )

    await query.answer(
        results,
        cache_time=1,
        is_personal=True
    )