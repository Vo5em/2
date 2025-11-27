import aiohttp
from aiogram import Router
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent, InputMediaAudio,
    ChosenInlineResult
)
from config import bot

from app.database.requests import (
    search_soundcloud, search_skysound,
    get_soundcloud_mp3_url, get_skysound_mp3,
    rank_tracks_by_similarity
)

router = Router()
TRACKS = {}


async def resolve_mp3_url(track):
    if track["source"] == "SoundCloud":
        return await get_soundcloud_mp3_url(track["url"])

    if track["source"] == "SkySound":
        return await get_skysound_mp3(track["url"])

    return None


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
    for i, t in enumerate(tracks[:18]):
        tid = f"{q.from_user.id}:{i}"
        TRACKS[tid] = t

        results.append(
            InlineQueryResultArticle(
                id=tid,
                title=f"{t['artist']} — {t['title']}",
                description=f"{t['source']} / {t['duration']}",
                thumb_url=t["thumb"],
                input_message_content=InputTextMessageContent(
                    message_text="⏳ Загрузка трека…"
                )
            )
        )

    await q.answer(results, cache_time=0)


@router.chosen_inline_result()
async def chosen(result: ChosenInlineResult):
    track = TRACKS.get(result.result_id)
    if not track:
        return

    inline_id = result.inline_message_id
    if not inline_id:
        return

    # получаем прямой mp3 URL
    mp3_url = await resolve_mp3_url(track)
    if not mp3_url:
        await bot.edit_message_text(
            "❌ Ошибка загрузки трека",
            inline_message_id=inline_id
        )
        return

    # заменяем текст → аудио через URL
    await bot.edit_message_media(
        inline_message_id=inline_id,
        media=InputMediaAudio(
            media=mp3_url,
            title=track["title"],
            performer=track["artist"],
            thumbnail=track["thumb"]
        )
    )

