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

    # Быстрый поиск метаданных (не качаем mp3 здесь)
    tracks = []
    try:
        tracks += await search_skysound(q)
        tracks += await search_soundcloud(q)
    except Exception as e:
        # если поиск упал — вернём пустой список, но не позволим падать
        print("Search error:", e)
        await query.answer([], cache_time=0)
        return

    if not tracks:
        await query.answer([], cache_time=0)
        return

    tracks = rank_tracks_by_similarity(q, tracks)

    # Ограничиваем количество проверяемых треков, чтобы не тянуть поиски слишком долго
    candidates = tracks[:10]

    # Получаем mp3_url параллельно, но с ограничением concurrency
    sem = asyncio.Semaphore(6)

    async def _fast_get(track):
        async with sem:
            return track, await _extract_mp3_url(track)

    tasks = [asyncio.create_task(_fast_get(t)) for t in candidates]
    results = await asyncio.gather(*tasks)

    iq_results = []
    for idx, (track, mp3_url) in enumerate(results):
        if not mp3_url:
            continue
        title = f"{track.get('artist','?')} — {track.get('title','?')}"
        ttumb = FSInputFile("ttumb.jpg")
        # InlineQueryResultAudio — Telegram сам вставит аудио в чат (личка, saved, группы)
        iq_results.append(InlineQueryResultAudio(
            id=str(idx),
            audio_url=mp3_url,
            title=track.get("title",""),
            performer=track.get("artist",""),
            thumb=ttumb
        ))
        # если нужно совсем быстро — можно break после 1 результата
        # break

    if not iq_results:
        # если не нашли ни одного mp3 — вернём быстрые текстовые карточки (пользователь увидит "Подождите" не нужно)
        # но пользователь просил сразу аудио — здесь пусто
        await query.answer([], cache_time=0)
        return

    # Отвечаем результатами — Telegram вставит аудио
    await query.answer(iq_results, cache_time=0, is_personal=True)