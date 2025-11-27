import re
import aiohttp
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InlineKeyboardButton, InlineKeyboardMarkup,
    InputTextMessageContent, InputMediaAudio,
    BufferedInputFile, Message
)
from config import bot

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url
from app.database.requests import rank_tracks_by_similarity


router = Router()

TRACKS_TEMP: dict[str, dict] = {}


async def fetch_mp3(t):
    """Скачивает mp3 файл"""
    if t["source"] == "SoundCloud":
        mp3_url = await get_soundcloud_mp3_url(t["url"])
    else:
        mp3_url = t["url"]

    async with aiohttp.ClientSession() as s:
        async with s.get(mp3_url) as r:
            return await r.read()


async def fetch_thumb(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status == 200:
                    return await r.read()
    except:
        return None
    return None


# ==========================
# 1. INLINE SEARCH
# ==========================
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

        TRACKS_TEMP[tid] = {
            "artist": t["artist"],
            "title": t["title"],
            "thumb": t["thumb"],
            "source": t["source"],
            "url": t["url"]
        }

        results.append(
            InlineQueryResultArticle(
                id=tid,
                title=f"{t['artist']} — {t['title']}",
                description=t["source"],
                thumb_url=t["thumb"],
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Загружаю…", callback_data="stub")]
                    ]
                ),
                input_message_content=InputTextMessageContent(
                    message_text=f"Загружаю {t['artist']} — {t['title']}…"
                )
            )
        )

    await q.answer(results, cache_time=1)


# ==========================
# 2. ПОЛЬЗОВАТЕЛЬ ВЫБРАЛ (как у конкурента)
# ==========================
@router.message(F.via_bot)
async def handle_inline_sent_message(msg: Message):

    print("\n========= VIA BOT HANDLER =========")
    print("Handler triggered for message id:", msg.message_id)

    # Проверяем via_bot факт
    if msg.via_bot:
        print("✔ via_bot exists:", msg.via_bot.id, msg.via_bot.username)
    else:
        print("❌ ERROR: msg.via_bot == None (THIS SHOULD NOT HAPPEN!)")
        return

    print("Message text:", msg.text)
    print("From user:", msg.from_user.id)
    print("Chat ID:", msg.chat.id)
    print("===================================")

    text = msg.text or ""
    user_id = msg.from_user.id

    # парсим track_id
    # text == "Загружаю ARTIST — TITLE…"
    track_id = None
    for tid in TRACKS_TEMP.keys():
        if tid.startswith(f"{user_id}_"):
            track_id = tid
            break

    if not track_id:
        return

    track = TRACKS_TEMP.get(track_id)
    if not track:
        return

    chat_id = msg.chat.id
    message_id = msg.message_id

    # обновляем сообщение
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text="⏳ Скачиваю…"
    )

    # mp3
    audio_bytes = await fetch_mp3(track)

    # обложка
    cover_bytes = await fetch_thumb(track["thumb"])

    media = InputMediaAudio(
        media=BufferedInputFile(audio_bytes, "track.mp3"),
        title=track["title"],
        performer=track["artist"],
        thumbnail=(
            BufferedInputFile(cover_bytes, "cover.jpg")
            if cover_bytes else None
        )
    )

    # заменяем сообщение на аудио
    await bot.edit_message_media(
        chat_id=chat_id,
        message_id=message_id,
        media=media
    )

