import re
import aiohttp
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent, InputMediaAudio,
    BufferedInputFile, Message
)
from config import bot

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url
from app.database.requests import rank_tracks_by_similarity

router = Router()

# tid → track-info
TRACKS_TEMP: dict[str, dict] = {}


# ===========================
# СКАЧИВАНИЕ MP3
# ===========================
async def fetch_mp3(t):
    if t["source"] == "SoundCloud":
        mp3_url = await get_soundcloud_mp3_url(t["url"])
    else:
        mp3_url = t["url"]

    async with aiohttp.ClientSession() as s:
        async with s.get(mp3_url) as r:
            return await r.read()


# ===========================
# СКАЧИВАНИЕ ОБЛОЖКИ
# ===========================
async def fetch_thumb(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status == 200:
                    return await r.read()
    except:
        pass
    return None


# ==========================================
# 1. INLINE SEARCH (показываем обложки)
# ==========================================
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

    for i, t in enumerate(tracks[:20]):
        tid = f"{q.from_user.id}_{i}"

        # сохраняем трек в кэш
        TRACKS_TEMP[tid] = t

        results.append(
            InlineQueryResultArticle(
                id=tid,
                title=f"{t['artist']} — {t['title']}",
                description=t["source"],
                thumb_url=t["thumb"],
                input_message_content=InputTextMessageContent(
                    message_text=f"[id:{tid}] ⏳ Загружаю {t['artist']} — {t['title']}…"
                )
            )
        )

    await q.answer(results, cache_time=0)


# ==============================================================
# 2. ПОЛЬЗОВАТЕЛЬ ОТПРАВИЛ INLINE ARTICLE (тут мы получаем chat_id)
# ==============================================================

@router.message(F.via_bot)
async def on_inline_message(msg: Message):

    print("\n============= VIA BOT =============")
    print("via_bot:", msg.via_bot)
    print("chat_id:", msg.chat.id)
    print("message_id:", msg.message_id)
    print("text:", msg.text)
    print("===================================\n")

    if not msg.text:
        return

    # -----------------------------------------------------------
    # ВАЖНО: извлекаем tid прямо из текста
    # пример: "[id:123_4] ⏳ Загружаю Artist — Title…"
    # -----------------------------------------------------------
    m = re.search(r"\[id:(.+?)\]", msg.text)
    if not m:
        print("❌ tid not found in message text")
        return

    tid = m.group(1)

    track = TRACKS_TEMP.get(tid)
    if not track:
        print("❌ track not found in TRACKS_TEMP:", tid)
        return

    chat_id = msg.chat.id
    message_id = msg.message_id

    # 1) обновляем текст → "скачиваю"
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text="⏳ Скачиваю…"
    )

    # 2) скачиваем mp3
    try:
        audio_bytes = await fetch_mp3(track)
    except Exception as e:
        print("MP3 ERROR:", e)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="❌ Ошибка загрузки файла"
        )
        return

    # 3) скачиваем обложку
    cover_bytes = await fetch_thumb(track["thumb"])

    # 4) формируем audio media
    media = InputMediaAudio(
        media=BufferedInputFile(audio_bytes, "track.mp3"),
        title=track["title"],
        performer=track["artist"],
        thumbnail=BufferedInputFile(cover_bytes, "cover.jpg") if cover_bytes else None
    )

    # 5) заменяем текстовое сообщение → аудио
    try:
        await bot.edit_message_media(
            chat_id=chat_id,
            message_id=message_id,
            media=media
        )
    except Exception as e:
        print("EDIT MEDIA ERROR:", e)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="❌ Ошибка при отправке аудио"
        )
        return

