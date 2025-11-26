import io
import re
import aiohttp
import asyncio
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent, ChosenInlineResult
)
from aiogram.types.input_file import FSInputFile
from config import bot

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
async def inline_search(query: InlineQuery):
    q = query.query.strip()
    if not q:
        return await query.answer([], cache_time=0)

    tracks = await search_soundcloud(q) + await search_skysound(q)

    results = []

    for idx, track in enumerate(tracks[:30]):
        _mp3_cache[(query.from_user.id, idx)] = track

        results.append(
            InlineQueryResultArticle(
                id=str(idx),
                title=f"{track['artist']} — {track['title']}",
                description=track["duration"],
                thumbnail_url=track.get("thumb"),
                input_message_content=InputTextMessageContent(
                    message_text=f"Загружаю трек..."
                )
            )
        )

    await query.answer(results, cache_time=0)



# 2. ВЫБОР ИНЛАЙН РЕЗУЛЬТАТА — вот этот обработчик ты забыл!
@router.chosen_inline_result()
async def chosen_result(result: ChosenInlineResult):
    user_id = result.from_user.id
    idx = int(result.result_id)

    track = _mp3_cache.get((user_id, idx))
    if not track:
        return

    # получаем mp3 ссылку
    mp3_url = await extract_mp3_url(track)
    if not mp3_url:
        return

    # скачиваем
    async with aiohttp.ClientSession() as session:
        async with session.get(mp3_url) as resp:
            audio_bytes = await resp.read()

    bio = io.BytesIO(audio_bytes)
    bio.name = "track.mp3"

    audio_file = FSInputFile(bio, filename="track.mp3")

    thumb = FSInputFile("ttumb.jpg")

    # отправляем пользователю аудио
    await bot.send_audio(
        chat_id=user_id,
        audio=FSInputFile(bio),
        title=track["title"],
        performer=track["artist"],
        thumbnail=thumb,
    )

