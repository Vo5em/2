import re
import asyncio
import aiohttp
import tempfile
from functools import lru_cache
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

# кеш mp3_url: LRU (память процесса). Можно заменить на Redis для кластеров.
@router.chosen_inline_result()
async def chosen_inline(chosen: ChosenInlineResult, bot: bot):
    user_id = chosen.from_user.id
    idx = int(chosen.result_id)

    if user_id not in user_tracks:
        return

    track = user_tracks[user_id][idx]

    # 1) Сразу показываем временное сообщение
    temp = await bot.send_message(
        chat_id=user_id,
        text=f"⏳ Загружаю трек...\n<b>{track['artist']} — {track['title']}</b>",
        parse_mode="HTML"
    )

    try:
        # 2) Качаем MP3
        url = track["url"]

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
                chat_id=user_id,
                message_id=temp.message_id,
                text="❌ MP3 не найден."
            )
            return

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://soundcloud.com/" if track["source"] == "SoundCloud" else "https://skysound7.com/"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(mp3_url, headers=headers, timeout=30) as resp:
                audio_bytes = await resp.read()

        if len(audio_bytes) < 50000:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=temp.message_id,
                text="❌ Файл повреждён."
            )
            return

        # Записываем временно
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            mp3_path = tmp.name

        audio = FSInputFile(mp3_path)

        # 3) ПОДМЕНА сообщения текст → аудио
        await bot.edit_message_media(
            chat_id=user_id,
            message_id=temp.message_id,
            media=InputMediaAudio(
                media=audio,
                performer=track['artist'],
                title=track['title'],
                caption='<a href="https://t.me/eschalon">eschalon</a>',
                parse_mode="HTML"
            )
        )

    except Exception as e:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=temp.message_id,
            text="❌ Ошибка загрузки."
        )
        print("❌ ERROR:", e)