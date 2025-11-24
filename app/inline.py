import re
import asyncio
import aiohttp
import tempfile
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ChosenInlineResult,
    InputMediaAudio,
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

# =========================
#   INLINE SEARCH (ОБЯЗАТЕЛЬНО!)
# =========================
@router.inline_query()
async def inline_search(query: InlineQuery):
    q = query.query.strip()

    if not q:
        await query.answer([], cache_time=0)
        return

    # ЛОГ
    print("INLINE SEARCH:", q)

    # быстрый поиск
    tracks = []
    tracks += await search_skysound(q)
    tracks += await search_soundcloud(q)

    if not tracks:
        await query.answer([], cache_time=0)
        return

    # сортировка по совпадению
    tracks = rank_tracks_by_similarity(q, tracks)

    # сохраняем результаты для chosen_inline_result
    user_tracks[query.from_user.id] = tracks

    results = []
    for idx, t in enumerate(tracks[:20]):
        title = f"{t['artist']} — {t['title']}"

        results.append(
            InlineQueryResultArticle(
                id=str(idx),
                title=title,
                description=f"⏱ {t['duration']}",
                input_message_content=InputTextMessageContent(
                    message_text=f"⏳ Загружаю...\n{title}"
                )
            )
        )

    await query.answer(results, cache_time=0)



# =========================
#     CHOSEN INLINE
# =========================
@router.chosen_inline_result()
async def chosen_inline(chosen: ChosenInlineResult):
    user_id = chosen.from_user.id
    idx = int(chosen.result_id)

    if user_id not in user_tracks:
        return

    track = user_tracks[user_id][idx]

    msg_id = chosen.inline_message_id
    if not msg_id:
        print("❌ inline_message_id отсутствует, нельзя редактировать сообщение")
        return

    # 1) Поставим временный текст в том же сообщении
    await bot.edit_message_text(
        inline_message_id=msg_id,
        text=f"⏳ Загружаю...\n{track['artist']} — {track['title']}"
    )

    try:
        # === загрузка MP3 ===
        url = track["url"]

        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(url)
        else:
            async with aiohttp.ClientSession() as s:
                async with s.get(url) as resp:
                    html = await resp.text()
            links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            mp3_url = links[0] if links else None

        if not mp3_url:
            await bot.edit_message_text(
                inline_message_id=msg_id,
                text="❌ MP3 не найден."
            )
            return

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://soundcloud.com/" if track["source"] == "SoundCloud" else "https://skysound7.com/"
        }

        async with aiohttp.ClientSession() as s:
            async with s.get(mp3_url, headers=headers) as resp:
                audio_bytes = await resp.read()

        if len(audio_bytes) < 50000:
            await bot.edit_message_text(
                inline_message_id=msg_id,
                text="❌ Файл повреждён."
            )
            return

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
            f.write(audio_bytes)
            mp3_path = f.name

        audio = FSInputFile(mp3_path)

        # 2) ПОДМЕНА ТЕКСТА → АУДИО
        await bot.edit_message_media(
            inline_message_id=msg_id,
            media=InputMediaAudio(
                media=audio,
                title=track['title'],
                performer=track['artist'],
                caption='<a href="https://t.me/eschalon">eschalon</a>',
                parse_mode="HTML"
            )
        )

    except Exception as e:
        print("❌ ERROR:", e)
        await bot.edit_message_text(
            inline_message_id=msg_id,
            text="❌ Ошибка загрузки."
        )