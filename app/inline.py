import uuid
import re
import tempfile
import aiohttp
from aiogram import Router, F
from aiogram.types import (
    InlineQuery,
    FSInputFile,
    Message,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Update,
    ChosenInlineResult
)
from app.database.requests import search_skysound, search_soundcloud, rank_tracks_by_similarity, get_soundcloud_mp3_url
from app.database.requests import duration_to_seconds


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


@router.message(F.text.startswith("Подождите"))
async def handle_inline_audio(message: Message):
    text = message.text.split("\n", 1)
    if len(text) < 2:
        return

    full_title = text[1].strip()
    user_id = message.from_user.id

    if user_id not in user_tracks:
        return

    selected_track = None
    for t in user_tracks[user_id]:
        if f"{t['artist']} — {t['title']}" == full_title:
            selected_track = t
            break

    if not selected_track:
        await message.edit_text("❌ Трек не найден.")
        return

    track = selected_track
    url = track["url"]

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
            await message.edit_text("❌ MP3 не найден.")
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
            await message.edit_text("❌ Файл повреждён.")
            return

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        audio = FSInputFile(tmp_path, filename=f"{track['artist']} — {track['title']}.mp3")
        thumb = FSInputFile("ttumb.jpg")

        await message.delete()
        await message.answer_audio(
            audio=audio,
            title=track['title'],
            performer=track['artist'],
            thumb=thumb,
            caption=f'<a href="https://t.me/eschalon">eschalon</a>, <a href="t.me/eschalonmusicbot">music</a>',
            parse_mode="HTML"
        )

    except Exception as e:
        print("ИНЛАЙН ОШИБКА:", e)
        await message.edit_text("❌ Ошибка загрузки.")

@router.chosen_inline_result()
async def chosen_inline(chosen: ChosenInlineResult):
    print("🔥 CHOSEN RESULT:")
    print("query:", chosen.query)
    print("result_id:", chosen.result_id)
    print("from:", chosen.from_user.id)
    print("inline_message_id:", chosen.inline_message_id)