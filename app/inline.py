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



@router.chosen_inline_result()
async def chosen_inline(chosen: ChosenInlineResult, bot: bot):
    user_id = chosen.from_user.id
    idx = int(chosen.result_id)

    if user_id not in user_tracks:
        return

    track = user_tracks[user_id][idx]
    url = track["url"]

    try:
        # === 1. получаем mp3 url ===
        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(url)
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    html = await resp.text()
            mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            mp3_url = mp3_links[0] if mp3_links else None

        if not mp3_url:
            await bot.send_message(user_id, "❌ MP3 не найден.")
            return

        # === 2. скачиваем mp3 ===
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://soundcloud.com/" if track["source"] == "SoundCloud" else "https://skysound7.com/"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(mp3_url, headers=headers, timeout=30) as resp:
                audio_bytes = await resp.read()

        if len(audio_bytes) < 50000:
            await bot.send_message(user_id, "❌ Файл повреждён.")
            return

        # === 3. сохраняем файл ===
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            mp3_path = tmp.name

        audio = FSInputFile(mp3_path)

        # === 4. ВАЖНО: сразу отправляем аудио (БЕЗ edit) ===
        await bot.send_audio(
            chat_id=user_id,
            audio=audio,
            performer=track['artist'],
            title=track['title'],
            caption='<a href="https://t.me/eschalon">eschalon</a>',
            parse_mode="HTML"
        )

    except Exception as e:
        print("❌ ERROR:", e)
        await bot.send_message(user_id, "❌ Ошибка загрузки трека.")