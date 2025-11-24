import re
import tempfile
import aiohttp
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    FSInputFile,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InputMediaAudio,
    ChosenInlineResult
)
from app.database.requests import search_skysound, search_soundcloud, rank_tracks_by_similarity, get_soundcloud_mp3_url
from config import bot


router = Router()

user_tracks = {}

# ======== INLINE =========
@router.inline_query()
async def inline_search(query: InlineQuery):
    text = query.query.strip()

    if not text:
        await query.answer([], cache_time=1)
        return

    tracks = []
    tracks += await search_skysound(text)
    tracks += await search_soundcloud(text)

    if not tracks:
        await query.answer([], cache_time=1)
        return

    tracks = rank_tracks_by_similarity(text, tracks)
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
                    message_text=f"Подождите...\n{title}",
                )
            )
        )

    await query.answer(results, cache_time=1)


@router.chosen_inline_result()
async def chosen_inline(chosen: ChosenInlineResult, bot: bot):
    print("🔥 CHOSEN RESULT:")
    print("query:", chosen.query)
    print("result_id:", chosen.result_id)
    print("from:", chosen.from_user.id)
    print("inline_message_id:", chosen.inline_message_id)

    user_id = chosen.from_user.id
    idx = int(chosen.result_id)

    if user_id not in user_tracks:
        return

    track = user_tracks[user_id][idx]
    url = track["url"]

    # ---- сначала редактируем заглушку, чтобы пользователь видел прогресс ----
    try:
        await bot.edit_message_text(
            inline_message_id=chosen.inline_message_id,
            text="Загружаю аудио…"
        )
    except:
        pass

    try:
        # === получаем mp3 URL ===
        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(url)
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    html = await resp.text()
            mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            mp3_url = mp3_links[0] if mp3_links else None

        if not mp3_url:
            await bot.edit_message_text(
                inline_message_id=chosen.inline_message_id,
                text="❌ MP3 не найден."
            )
            return

        # === скачиваем mp3 ===
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://soundcloud.com/" if track["source"] == "SoundCloud" else "https://skysound7.com/"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(mp3_url, headers=headers, timeout=30) as resp:
                audio_bytes = await resp.read()

        if len(audio_bytes) < 50000:
            await bot.edit_message_text(
                inline_message_id=chosen.inline_message_id,
                text="❌ Файл повреждён."
            )
            return

        # === временный файл ===
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # === готовим аудио ===
        audio = FSInputFile(tmp_path, filename=f"{track['artist']} — {track['title']}.mp3")
        thumb = FSInputFile("ttumb.jpg")

        # === заменяем заглушку на аудио ===
        await bot.edit_message_media(
            inline_message_id=chosen.inline_message_id,
            media=InputMediaAudio(
                media=audio,
                title=track['title'],
                performer=track['artist'],
                caption='<a href="https://t.me/eschalon">eschalon</a>, <a href="t.me/eschalonmusicbot">music</a>',
                parse_mode="HTML",
                thumb=thumb
            )
        )

    except Exception as e:
        print("ИНЛАЙН ОШИБКА:", e)
        await bot.edit_message_text(
            inline_message_id=chosen.inline_message_id,
            text="❌ Ошибка загрузки."
        )