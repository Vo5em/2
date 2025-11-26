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

# временный кэш
AUDIO_CACHE = {}  # url -> file_id
TRACKS_TEMP = {}  # result_id -> track_dict


@router.inline_query()
async def inline_search(q: InlineQuery):
    text = q.query.strip()
    if not text:
        return await q.answer([], cache_time=0)

    # выполняем поиск
    tracks = []
    tracks += await search_soundcloud(text)
    tracks += await search_skysound(text)

    results = []

    for i, t in enumerate(tracks[:30]):
        uid = f"track_{i}"

        TRACKS_TEMP[uid] = t  # сохраним весь трек

        # обложка грузится через thumbnail_url
        results.append(
            InlineQueryResultArticle(
                id=uid,
                title=f"{t['artist']} — {t['title']}",
                description=t["duration"],
                thumbnail_url=t.get("thumb"),
                input_message_content=InputTextMessageContent(
                    message_text=f"▶ {t['artist']} — {t['title']}"
                )
            )
        )

    await q.answer(results, cache_time=0)



@router.chosen_inline_result()
async def on_choose(res: ChosenInlineResult):
    track = TRACKS_TEMP.get(res.result_id)
    if not track:
        return

    chat_id = res.from_user.id

    url = track["url"]

    # если файл был отправлен ранее — отправим из кэша
    if url in AUDIO_CACHE:
        await res.bot.send_audio(
            chat_id,
            audio=AUDIO_CACHE[url],
            title=track["title"],
            performer=track["artist"],
            thumbnail=track.get("thumb")
        )
        return

    # иначе — качаем mp3
    mp3 = await get_soundcloud_mp3_url(url)

    # отправляем
    sent = await res.bot.send_audio(
        chat_id,
        audio=mp3,
        title=track["title"],
        performer=track["artist"],
        thumbnail=track.get("thumb")
    )

    # кэшируем file_id
    AUDIO_CACHE[url] = sent.audio.file_id

