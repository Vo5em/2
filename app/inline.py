import io
import re
import asyncio
import aiohttp
from aiogram import Router, F
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineQueryResultCachedAudio,
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineQueryResultAudio,
    InlineKeyboardButton,
    FSInputFile
)
from app.database.requests import (
    search_skysound,
    search_soundcloud,
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
async def inline_search(query: InlineQuery):
    q = query.query.strip()
    if not q:
        return await query.answer([], cache_time=0)

    tracks = []
    tracks += await search_soundcloud(q)
    tracks += await search_skysound(q)

    results = []

    for idx, track in enumerate(tracks[:20]):   # 20 результатов чтобы быстрее
        # Пытаемся сразу получить прямой mp3
        mp3_url = await _extract_mp3_url(track)

        if mp3_url:
            # Если есть прямой mp3 — отдаём сразу аудио
            results.append(
                InlineQueryResultAudio(
                    id=str(idx),
                    audio_url=mp3_url,
                    title=track["title"],
                    performer=track["artist"],
                    thumbnail_url=track.get("thumb")  # твоя обложка в inline
                )
            )
        else:
            # если mp3 нет — показываем без возможности отправить
            results.append(
                InlineQueryResultArticle(
                    id=f"nf{idx}",
                    title=f"{track['artist']} — {track['title']}",
                    description="MP3 не найден",
                    thumbnail_url=track.get("thumb"),
                    input_message_content=InputTextMessageContent(
                        message_text="❌ Не удалось получить mp3"
                    )
                )
            )

    await query.answer(results, cache_time=0)


