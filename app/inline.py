import re
import io
import os
import tempfile
import aiohttp
import asyncio
import traceback
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,ChosenInlineResult,
    InputTextMessageContent,InlineKeyboardMarkup,InlineKeyboardButton,BufferedInputFile, CallbackQuery
)
from aiogram.types.input_file import FSInputFile
from config import bot

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url, get_skysound_mp3
from app.database.requests import rank_tracks_by_similarity


router = Router()
user_tracks ={}

async def fetch_mp3(track):
    url = track["url"]

    if track["source"] == "SoundCloud":
        mp3 = await get_soundcloud_mp3_url(url)
        if not mp3:
            raise Exception("SC mp3 not found")
        final = mp3

    else:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                html = await r.text()
        links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
        if not links:
            raise Exception("SkySound mp3 not found")
        final = links[0]

    async with aiohttp.ClientSession() as session:
        async with session.get(final) as r:
            if r.status != 200:
                raise Exception("download error")
            return await r.read()


@router.inline_query()
async def inline_search(inline_query: InlineQuery):
    query = inline_query.query.strip()

    if not query:
        return await inline_query.answer([])

    tracks = []
    tracks += await search_skysound(query)
    tracks += await search_soundcloud(query)

    if not tracks:
        return await inline_query.answer([
            InlineQueryResultArticle(
                id="notfound",
                title="Ничего не найдено",
                input_message_content=InputTextMessageContent(message_text=
                    f"По запросу «{query}» ничего не найдено"
                )
            )
        ])

    # сортировка
    tracks = rank_tracks_by_similarity(query, tracks)

    results = []

    for i, t in enumerate(tracks[:15]):  # ограничение Telegram
        title = f"{t['artist']} — {t['title']}"

        results.append(
            InlineQueryResultArticle(
                id=str(i),
                title=title,
                description=f"Источник: {t['source']}",
                input_message_content=InputTextMessageContent(message_text=
                    f"🎵 {title}\nНажми ниже, чтобы получить аудио."
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="▶ Скачать трек",
                                switch_inline_query_current_chat=f"play_{i}"
                            )
                        ]
                    ]
                )
            )
        )

    # сохраняем список в память
    user_tracks[inline_query.from_user.id] = tracks

    await inline_query.answer(results, cache_time=1)

@router.chosen_inline_result()
async def chosen(res: ChosenInlineResult):
    user_id = res.from_user.id
    idx = int(res.result_id)

    track = user_tracks.get(user_id, [])[idx]

    # 1) скачиваем mp3
    mp3_bytes = await fetch_mp3(track)

    # 2) скачиваем обложку
    async with aiohttp.ClientSession() as s:
        async with s.get(track["thumb"]) as r:
            thumb_bytes = await r.read()

    # 3) отправляем аудио пользователю напрямую
    await res.bot.send_audio(
        chat_id=user_id,
        audio=BufferedInputFile(mp3_bytes, f"{track['title']}.mp3"),
        title=track["title"],
        performer=track["artist"],
        thumbnail=BufferedInputFile(thumb_bytes, "thumb.jpg"),
        caption="твой трек 🎵"
    )

    # 4) удаляем inline-сообщение => создаётся эффект “замены”
    if res.inline_message_id:
        try:
            await res.bot.edit_message_text(
                inline_message_id=res.inline_message_id,
                text="Отправляю аудио…"
            )
            await res.bot.delete_message(
                chat_id=user_id,
                message_id=res.inline_message_id
            )
        except:
            pass

