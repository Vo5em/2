import re
import aiohttp
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ChosenInlineResult,
    InputMediaAudio
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

# ===================== INLINE ======================
@router.inline_query()
async def inline_search(query: InlineQuery):
    text = query.query.strip()

    if not text:
        await query.answer([], cache_time=1)
        return

    # быстрый поиск — только названия, без MP3
    tracks = []
    tracks += await search_skysound(text)
    tracks += await search_soundcloud(text)

    if not tracks:
        await query.answer([], cache_time=1)
        return

    tracks = rank_tracks_by_similarity(text, tracks)

    user_tracks[query.from_user.id] = tracks  # сохраняем только метаданные

    results = []
    for idx, t in enumerate(tracks[:20]):
        title = f"{t['artist']} — {t['title']}"

        results.append(
            InlineQueryResultArticle(
                id=str(idx),
                title=title,
                description=f"⏱ {t['duration']}",
                input_message_content=InputTextMessageContent(
                    message_text=f"⏬ Загружаю трек...\n{title}",
                )
            )
        )

    await query.answer(results, cache_time=0)


@router.chosen_inline_result()
async def chosen(chosen: ChosenInlineResult):
    user_id = chosen.from_user.id
    idx = int(chosen.result_id)

    if user_id not in user_tracks:
        return

    track = user_tracks[user_id][idx]

    # получаем mp3 URL (теперь можно медленно)
    if track["source"] == "SoundCloud":
        mp3_url = await get_soundcloud_mp3_url(track["url"])
    else:
        async with aiohttp.ClientSession() as session:
            async with session.get(track["url"]) as resp:
                html = await resp.text()
        mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
        mp3_url = mp3_links[0] if mp3_links else None

    if not mp3_url:
        return

    # отправляем аудио туда, где был inline → via inline_message_id
    if chosen.inline_message_id:
        await bot.edit_message_media(
            inline_message_id=chosen.inline_message_id,
            media=InputMediaAudio(
                media=mp3_url,
                title=track["title"],
                performer=track["artist"]
            )
        )