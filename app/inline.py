import re
import tempfile
import asyncio
import aiohttp
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ChosenInlineResult,
    InlineQueryResultAudio,
    InlineQueryResultCachedDocument,
    BufferedInputFile,
    FSInputFile
)
from config import bot
from app.database.requests import (
    search_skysound,
    search_soundcloud,
    rank_tracks_by_similarity,
    get_soundcloud_mp3_url
)


router = Router()
user_tracks = {}

# -------- INLINE SEARCH ------------
# Параметры таймаутов — уменьши, если хочешь ещё быстрее, но риск пропустить треки вырастет
HTML_FETCH_TIMEOUT = 4      # seconds to fetch track page (fast)
MP3_HEAD_TIMEOUT = 6        # seconds to HEAD/mp3 check

# В памяти короткий кэш прямых mp3 ссылок (ключ = track['url'])
_mp3_cache: dict[str,str] = {}

async def _extract_mp3_url(track: dict) -> str | None:
    """Попытаться быстро получить прямой mp3 URL для трека.
       Возвращает строку URL или None.
       Быстро: короткие таймауты, кэш, не скачиваем весь файл.
    """
    key = track.get("url")
    if key in _mp3_cache:
        return _mp3_cache[key]

    try:
        if track.get("source") == "SoundCloud":
            # Использует твою функцию — ожидается, что она возвращает прямой mp3 URL
            mp3 = await asyncio.wait_for(get_soundcloud_mp3_url(key), timeout=MP3_HEAD_TIMEOUT)
            if mp3:
                _mp3_cache[key] = mp3
            return mp3
        else:
            timeout = aiohttp.ClientTimeout(total=HTML_FETCH_TIMEOUT)
            headers = {"User-Agent":"Mozilla/5.0"}
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(key, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()

            m = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            if not m:
                return None

            mp3_url = m[0]
            # Быстрая проверка доступности (HEAD or small GET)
            try:
                timeout2 = aiohttp.ClientTimeout(total=MP3_HEAD_TIMEOUT)
                async with aiohttp.ClientSession(timeout=timeout2) as sess:
                    async with sess.get(mp3_url, headers=headers) as r2:
                        if r2.status == 200:
                            # не читаем весь файл, но можно read a few bytes if needed
                            _mp3_cache[key] = mp3_url
                            return mp3_url
                        else:
                            return None
            except Exception:
                return None
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None


@router.inline_query()
async def inline_search_fast(query: InlineQuery):
    q = query.query.strip()
    if not q:
        await query.answer([], cache_time=0)
        return

    tracks = await search_soundcloud(q) + await search_skysound(q)
    if not tracks:
        await query.answer([], cache_time=0)
        return

    tracks = rank_tracks_by_similarity(q, tracks)
    track = tracks[0]  # берём лучший

    mp3_url = await _extract_mp3_url(track)
    if not mp3_url:
        await query.answer([], cache_time=0)
        return

    # Качаем MP3
    async with aiohttp.ClientSession() as session:
        async with session.get(mp3_url) as resp:
            audio_bytes = await resp.read()

    # Качаем обложку (если есть)
    cover_file = None
    if track.get("cover"):
        async with aiohttp.ClientSession() as session:
            async with session.get(track["cover"]) as resp:
                cover_bytes = await resp.read()

        cover_file = BufferedInputFile(cover_bytes, filename="cover.jpg")

    audio_file = BufferedInputFile(audio_bytes, filename="track.mp3")

    # INLINE RESULT — с обложкой НЕ РАБОТАЕТ, Telegram не поддерживает.
    # Поэтому делаем хитрость — отправляем аудио СРАЗУ пользователю:

    await bot.send_audio(
        chat_id=query.from_user.id,
        audio=audio_file,
        performer=track["artist"],
        title=track["title"],
        thumb=cover_file
    )

    # А в inline просто показываем кнопочку "Отправлено!"
    await query.answer([
        InlineQueryResultArticle(
            id="done",
            title="Трек загружен",
            input_message_content=InputTextMessageContent("✔ Трек отправлен вам в личку")
        )
    ], cache_time=0)