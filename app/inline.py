import re
import tempfile
import aiohttp
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ChosenInlineResult,
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
@router.inline_query()
async def inline_search(query: InlineQuery):
    text = query.query.strip()

    if not text:
        await query.answer([], cache_time=1)
        return

    # собираем результаты
    tracks = []
    tracks += await search_skysound(text)
    tracks += await search_soundcloud(text)

    if not tracks:
        await query.answer([], cache_time=1)
        return

    tracks = rank_tracks_by_similarity(text, tracks)

    # сохраняем треки за пользователем
    user_tracks[query.from_user.id] = tracks

    results = []
    for idx, track in enumerate(tracks[:20]):
        title = f"{track['artist']} — {track['title']}"
        results.append(
            InlineQueryResultArticle(
                id=str(idx),
                title=title,
                description=f"⏱ {track['duration']}",
                input_message_content=InputTextMessageContent(
                    message_text=f"⏳ Загружаю...\n{title}"
                )
            )
        )

    await query.answer(results, cache_time=1)


# -------- SEND AUDIO DIRECTLY --------
@router.chosen_inline_result()
async def chosen_inline(chosen: ChosenInlineResult, bot: bot):
    user_id = chosen.from_user.id
    idx = int(chosen.result_id)

    if user_id not in user_tracks:
        print("❌ tracks not found for user")
        return

    track = user_tracks[user_id][idx]
    url = track["url"]

    try:
        # --- получаем mp3 URL ---
        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(url)
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    html = await resp.text()
            mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            mp3_url = mp3_links[0] if mp3_links else None

        if not mp3_url:
            await bot.send_message(user_id, "❌ mp3 не найден.")
            return

        # --- качаем файлик ---
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://soundcloud.com/" if track["source"] == "SoundCloud" else "https://skysound7.com/"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(mp3_url, headers=headers, timeout=30) as resp:
                audio_bytes = await resp.read()

        # проверка
        if len(audio_bytes) < 50000:
            await bot.send_message(user_id, "❌ Файл поврежден.")
            return

        # --- сохраняем во временный файл ---
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            path = tmp.name

        # --- СРАЗУ отправляем АУДИО ---
        await bot.send_audio(
            chat_id=user_id,
            audio=FSInputFile(path),
            performer=track["artist"],
            title=track["title"],
            caption="🎵 @eschalonmusicbot"
        )

    except Exception as e:
        print("ERROR:", e)
        await bot.send_message(user_id, "❌ Ошибка загрузки трека.")