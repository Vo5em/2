import io
import re
import aiohttp
import asyncio
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent, Message
)
from aiogram.types.input_file import FSInputFile

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url

router = Router()

# хранилище найденных треков
TRACK_CACHE = {}

# таймауты
HTML_FETCH_TIMEOUT = 4
MP3_HEAD_TIMEOUT = 6

_mp3_cache = {}     # кеш прямых mp3 ссылок


# ========= ФУНКЦИЯ ПОЛУЧЕНИЯ ПРЯМОГО MP3 ========
async def extract_mp3_url(track: dict):
    url = track["url"]

    if url in _mp3_cache:
        return _mp3_cache[url]

    try:
        if track["source"] == "SoundCloud":
            mp3 = await asyncio.wait_for(
                get_soundcloud_mp3_url(url),
                timeout=MP3_HEAD_TIMEOUT
            )
            if mp3:
                _mp3_cache[url] = mp3
            return mp3

        timeout = aiohttp.ClientTimeout(total=HTML_FETCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url) as r:
                if r.status != 200:
                    return None
                html = await r.text()

        m = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
        if not m:
            return None

        mp3 = m[0]
        _mp3_cache[url] = mp3
        return mp3

    except:
        return None


# ========== INLINE SEARCH ============
@router.inline_query()
async def inline_search(q: InlineQuery):
    text = q.query.strip()
    if not text:
        return await q.answer([])

    tracks = []
    tracks += await search_soundcloud(text)
    tracks += await search_skysound(text)

    results = []

    for idx, track in enumerate(tracks[:30]):
        track_id = f"{q.from_user.id}:{idx}"
        TRACK_CACHE[track_id] = track

        results.append(
            InlineQueryResultArticle(
                id=track_id,
                title=f"{track['artist']} — {track['title']}",
                description=track["duration"],
                thumbnail_url=track.get("thumb"),
                input_message_content=InputTextMessageContent(
                    message_text=f"__dl__:{track_id}"
                )
            )
        )

    await q.answer(results, cache_time=0)


# ===== ПОСЛЕ ВЫБОРА ТРЕКА (ЛОВИМ ТЕКСТ "__dl__:id") =====
@router.message(F.text.startswith("__dl__:"))
async def deliver_audio(msg: Message):
    track_id = msg.text.split(":")[1]

    if track_id not in TRACK_CACHE:
        return await msg.answer("Ошибка: трек не найден в кэше")

    track = TRACK_CACHE[track_id]

    # получаем mp3
    mp3_url = await extract_mp3_url(track)
    if not mp3_url:
        return await msg.answer("❌ Не удалось получить mp3")

    async with aiohttp.ClientSession() as sess:
        async with sess.get(mp3_url) as r:
            audio_bytes = await r.read()

    bio = io.BytesIO(audio_bytes)
    bio.name = "track.mp3"

    audio_file = FSInputFile(bio)
    cover = FSInputFile("cover.jpg")   # ← ТВОЯ ОБЛОЖКА

    await msg.answer_audio(
        audio=audio_file,
        performer=track["artist"],
        title=track["title"],
        thumbnail=cover
    )

