import re
import aiohttp
import tempfile
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ChosenInlineResult,
    InputMediaAudio,
)
from app.database.requests import (
    search_skysound,
    search_soundcloud,
    rank_tracks_by_similarity,
    get_soundcloud_mp3_url,
)
from config import bot

router = Router()
user_tracks = {}


# ===================== INLINE ======================
@router.inline_query()
async def inline_search(query: InlineQuery):
    text = query.query.strip()

    if not text:
        await query.answer([], cache_time=1)
        return

    # Быстрый поиск — НИКАКИХ mp3 здесь!
    tracks = []
    tracks += await search_skysound(text)
    tracks += await search_soundcloud(text)

    if not tracks:
        await query.answer([], cache_time=1)
        return

    tracks = rank_tracks_by_similarity(text, tracks)

    user_tracks[query.id] = tracks  # сохраняем по query.id (правильнее)

    results = []

    for idx, t in enumerate(tracks[:20]):
        title = f"{t['artist']} — {t['title']}"

        # placeholder
        results.append(
            InlineQueryResultArticle(
                id=str(idx),
                title=title,
                description=f"⏱ {t['duration']}",
                input_message_content=InputTextMessageContent(
                    message_text=f"⏳ Загружаю трек...\n{title}"
                )
            )
        )

    await query.answer(results, cache_time=0, is_personal=True)


# ===================== CHOSEN ======================
@router.chosen_inline_result()
async def chosen(chosen: ChosenInlineResult):

    print("🔥 chosen_inline_result:")
    print("result_id:", chosen.result_id)
    print("inline_message_id:", chosen.inline_message_id)

    if chosen.inline_message_id is None:
        print("❌ НЕТ inline_message_id — невозможно вставить аудио")
        return

    tracks = user_tracks.get(chosen.inline_query_id)
    if not tracks:
        print("❌ Нет сохранённого списка треков")
        return

    idx = int(chosen.result_id)
    track = tracks[idx]

    # ============= ГРУЗИМ MP3 (только здесь!) =============
    url = track["url"]

    if track["source"] == "SoundCloud":
        mp3_url = await get_soundcloud_mp3_url(url)
    else:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                html = await resp.text()
        m = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
        mp3_url = m[0] if m else None

    if not mp3_url:
        await bot.edit_message_text(
            inline_message_id=chosen.inline_message_id,
            text="❌ Не удалось получить MP3."
        )
        return

    # Telegram принимает remote mp3 URL напрямую → НЕ нужно скачивать файл
    try:
        await bot.edit_message_media(
            inline_message_id=chosen.inline_message_id,
            media=InputMediaAudio(
                media=mp3_url,
                title=track["title"],
                performer=track["artist"],
                caption='<a href="https://t.me/eschalon">eschalon</a>',
                parse_mode="HTML"
            )
        )

    except Exception as e:
        print("❌ Ошибка edit_message_media:", e)
        await bot.edit_message_text(
            inline_message_id=chosen.inline_message_id,
            text="❌ Ошибка загрузки аудио."
        )