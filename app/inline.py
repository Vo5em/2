import re
import asyncio
import aiohttp
import tempfile
from aiogram import Router, F
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ChosenInlineResult,
    InputMediaAudio,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
    FSInputFile
)
from config import bot
from app.database.requests import (
    search_skysound,
    search_soundcloud,
    rank_tracks_by_similarity,
    get_soundcloud_mp3_url,
    download_track
)

router = Router()
user_tracks = {}

@router.inline_query()
async def inline_search(query: InlineQuery):
    text = query.query.strip()
    if not text:
        return

    tracks = []
    tracks += await search_skysound(text)
    tracks += await search_soundcloud(text)

    results = []

    for idx, t in enumerate(tracks):
        results.append(
            InlineQueryResultArticle(
                id=str(idx),
                title=f"{t['artist']} — {t['title']}",
                description="Нажми чтобы получить",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        f"🎧 <b>{t['artist']} — {t['title']}</b>\n"
                        f"Нажми кнопку ниже"
                    ),
                    parse_mode="HTML"
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(
                            text="🎵 Скачать",
                            callback_data=f"get:{idx}"
                        )
                    ]]
                )
            )
        )

    return await query.answer(results, cache_time=0, is_personal=True)

@router.callback_query(F.data.startswith("get:"))
async def callback_get_track(callback: CallbackQuery):
    user_id = callback.from_user.id
    idx = int(callback.data.split(":")[1])

    track = user_tracks.get(user_id, [])[idx]

    # 1) Удаляем “Нажми на меня”
    try:
        await callback.message.delete()
    except:
        pass

    # 2) Быстрее отправляем заглушку (0.1 сек)
    temp = await callback.message.answer(
        f"⏳ Загружаю...\n<b>{track['artist']} — {track['title']}</b>",
        parse_mode="HTML"
    )

    # 3) Скачиваем MP3
    mp3_bytes = await download_track(track["url"])

    # 4) Отправляем аудио
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
        tmp.write(mp3_bytes)
        path = tmp.name

    audio = FSInputFile(path)

    await temp.delete()

    await callback.message.answer_audio(
        audio=audio,
        performer=track["artist"],
        title=track["title"],
        caption='<a href="https://t.me/eschalon">eschalon</a>',
        parse_mode="HTML"
    )