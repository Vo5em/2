import re
import aiohttp
import tempfile
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

# =========================
#   INLINE SEARCH (ОБЯЗАТЕЛЬНО!)
# =========================
@router.inline_query()
async def inline_search(query: InlineQuery):
    text = query.query.strip()

    if not text:
        return await query.answer([], cache_time=1)

    tracks = []
    tracks += await search_skysound(text)
    tracks += await search_soundcloud(text)

    if not tracks:
        return await query.answer([], cache_time=1)

    tracks = rank_tracks_by_similarity(text, tracks)

    # сохраняем треки для пользователя
    user_tracks[query.from_user.id] = tracks

    results = []
    for i, tr in enumerate(tracks[:20]):
        title = f"{tr['artist']} — {tr['title']}"
        results.append(
            InlineQueryResultArticle(
                id=str(i),
                title=title,
                description=f"⏱ {tr['duration']}",
                input_message_content=InputTextMessageContent(
                    message_text=f"⏳ Загружаю трек...\n{title}"
                )
            )
        )

    await query.answer(results, cache_time=1)



# ==========================
#    SEND AUDIO DIRECTLY
# ==========================
@router.chosen_inline_result()
async def chosen_inline(chosen: ChosenInlineResult, bot: bot):
    user_id = chosen.from_user.id
    idx = int(chosen.result_id)

    if user_id not in user_tracks:
        return

    track = user_tracks[user_id][idx]
    url = track["url"]

    # Отправляем сообщение что началась загрузка
    loading_msg = await bot.send_message(
        chat_id=user_id,
        text=f"🎧 Загружаю:\n<b>{track['artist']} — {track['title']}</b>",
        parse_mode="HTML"
    )

    try:
        # --- Получаем mp3 URL ---
        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(url)
        else:
            # SkySound: ищем mp3 ссылку в html
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as r:
                    html = await r.text()
            links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            mp3_url = links[0] if links else None

        if not mp3_url:
            return await loading_msg.edit_text("❌ mp3 не найден")

        # --- Качаем mp3 ---
        headers = {
            "User-Agent": "Mozilla/5.0",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(mp3_url, headers=headers, timeout=30) as r:
                audio_bytes = await r.read()

        if len(audio_bytes) < 50000:
            return await loading_msg.edit_text("❌ повреждённый файл")

        # временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            path = tmp.name

        audio = FSInputFile(path)

        # --- ОТПРАВЛЯЕМ АУДИО ---
        await bot.send_audio(
            chat_id=user_id,
            audio=audio,
            performer=track["artist"],
            title=track["title"],
            caption='<a href="https://t.me/eschalon">eschalon</a>',
            parse_mode="HTML"
        )

        # удаляем сообщение "загружаю"
        await loading_msg.delete()

    except Exception as e:
        await loading_msg.edit_text("❌ ошибка загрузки")
        print("ERROR:", e)