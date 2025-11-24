import re
import asyncio
import aiohttp
from functools import lru_cache
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ChosenInlineResult,
    InputMediaAudio,
)
from config import bot
from app.database.requests import (
    search_skysound,
    search_soundcloud,
    rank_tracks_by_similarity,
    get_soundcloud_mp3_url,
)

router = Router()

# кеш mp3_url: LRU (память процесса). Можно заменить на Redis для кластеров.
@lru_cache(maxsize=4096)
def cached_mp3_url(key: str) -> str | None:
    # Это заглушка — lru_cache применится к возвращаемому значению из wrapper ниже.
    return None

# Вспомогательная неблокирующая обёртка (чтобы использовать lru_cache с async)
async def get_mp3_url_cached(track_url: str, source: str) -> str | None:
    cache_key = f"{source}:{track_url}"
    res = cached_mp3_url.__wrapped__(cache_key)  # обращаемся к внутренней реализации
    if res:
        return res

    # если нет в кеше — получаем (внешний вызов)
    mp3 = None
    if source == "SoundCloud":
        mp3 = await get_soundcloud_mp3_url(track_url)
    else:
        # короткий html fetch с таймаутом
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(track_url, headers={"User-Agent":"Mozilla/5.0"}, timeout=8) as resp:
                html = await resp.text()
        m = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
        mp3 = m[0] if m else None

    # положим в LRU (manually)
    if mp3:
        # реализуем запись в lru_cache: через вызов функции-обёртки (trick)
        # мы заменим cached_mp3_url.__wrapped__ через setattr не нужно; проще:
        cached_mp3_url.cache_clear()  # аккуратно: можно улучшить per-key cache
        # Для простоты: сохраняем через отдель dict
        _simple_cache[cache_key] = mp3

    return mp3

# более надёжный per-key кеш (проще в управлении)
_simple_cache: dict[str, str] = {}

async def get_mp3_url_fast(track_url: str, source: str) -> str | None:
    key = f"{source}:{track_url}"
    if key in _simple_cache:
        return _simple_cache[key]

    try:
        if source == "SoundCloud":
            mp3 = await get_soundcloud_mp3_url(track_url)
        else:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(track_url, headers={"User-Agent":"Mozilla/5.0"}, timeout=8) as resp:
                    html = await resp.text()
            m = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            mp3 = m[0] if m else None

        if mp3:
            # кэшируем (TTL можно внедрить)
            _simple_cache[key] = mp3
        return mp3
    except Exception:
        return None

# Ограничаем кол-во одновременных fetch-работ (чтобы не DDoS-ить источники)
_fetch_semaphore = asyncio.Semaphore(6)

# Сохраняем короткий кэш результатов поиска по user (используем query.id или from_user.id)
user_tracks: dict[str, list] = {}

# ========== INLINE (быстро) ==========
@router.inline_query()
async def inline_search(query: InlineQuery):
    text = query.query.strip()
    if not text:
        await query.answer([], cache_time=1)
        return

    # быстрый поиск (метаданные только)
    tracks = []
    tracks += await search_skysound(text)
    tracks += await search_soundcloud(text)
    if not tracks:
        await query.answer([], cache_time=1)
        return

    tracks = rank_tracks_by_similarity(text, tracks)
    # сохраняем список по query.id (лучше, чем по user.id, чтобы избежать гонок)
    user_tracks[query.id] = tracks

    results = []
    for idx, t in enumerate(tracks[:20]):
        title = f"{t['artist']} — {t['title']}"
        results.append(
            InlineQueryResultArticle(
                id=str(idx),
                title=title,
                description=f"⏱ {t.get('duration','?')}",
                input_message_content=InputTextMessageContent(
                    message_text=f"⏳ Нажмите чтобы получить\n{title}"
                )
            )
        )

    # cache_time минимален, is_personal True если нужно
    await query.answer(results, cache_time=0, is_personal=True)


# ========== CHOSEN (не блокируем UI) ==========
@router.chosen_inline_result()
async def chosen_inline(chosen: ChosenInlineResult):
    inline_msg_id = chosen.inline_message_id

    if not inline_msg_id:
        return

    rid = chosen.result_id          # "612345:7"
    qid, idx = rid.split(":")
    idx = int(idx)

    tracks = user_tracks.get(qid)
    if not tracks:
        return

    track = tracks[idx]

    asyncio.create_task(_fetch_and_replace_audio(inline_msg_id, track))


async def _fetch_and_replace_audio(inline_message_id: str, track: dict):
    """
    Фоновая задача: получает mp3_url (из кэша или сети) и вызывает edit_message_media.
    """
    async with _fetch_semaphore:
        mp3_url = await get_mp3_url_fast(track["url"], track.get("source", ""))

    if not mp3_url:
        # если mp3 не нашли — аккуратно заменим текст
        try:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text="❌ MP3 не найден."
            )
        except Exception:
            pass
        return

    # Теперь делаем замену: Telegram поддерживает remote mp3 URL в InputMediaAudio
    try:
        await bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaAudio(
                media=mp3_url,
                title=track.get("title"),
                performer=track.get("artist"),
                caption=track.get("caption") or '<a href="https://t.me/yourbot">yourbot</a>',
                parse_mode="HTML"
            )
        )
    except Exception as e:
        # Логируем, можно попытаться fallback (например, заменить текст на "ошибка")
        try:
            await bot.edit_message_text(inline_message_id=inline_message_id, text="❌ Ошибка загрузки аудио.")
        except Exception:
            pass