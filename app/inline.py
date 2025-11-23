import aiohttp
import re
from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup, InlineQueryResultAudio
from app.database.requests import search_skysound, search_soundcloud, rank_tracks_by_similarity, get_soundcloud_mp3_url
from app.database.requests import duration_to_seconds


router = Router()

user_tracks = {}

@router.inline_query()
async def inline_handler(query: InlineQuery):
    q = query.query.strip()
    if not q:
        await query.answer([], cache_time=1)
        return

    # --- Получаем треки как в обычном режиме ---
    tracks = []
    tracks += await search_skysound(q)
    tracks += await search_soundcloud(q)

    if not tracks:
        await query.answer([], cache_time=1, switch_pm_text="Ничего не найдено", switch_pm_parameter="start")
        return

    # --- Ранжирование как в твоем боте ---
    tracks = rank_tracks_by_similarity(q, tracks)

    results = []

    # --- Полное повторение твоей mp3-логики для каждого результата ---
    for i, track in enumerate(tracks[:25]):
        artist = track["artist"]
        title = track["title"]
        url = track["url"]

        try:
            # --- Получение mp3 URL ---
            if track["source"] == "SoundCloud":
                mp3_url = await get_soundcloud_mp3_url(url)
                if not mp3_url:
                    continue
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=15) as resp:
                        html = await resp.text()
                mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
                if not mp3_links:
                    continue
                mp3_url = mp3_links[0]

            # --- Конвертация длительности ---
            duration = duration_to_seconds(track.get("duration"))

            results.append(
                InlineQueryResultAudio(
                    id=str(i),
                    title=f"{artist} — {title}",
                    audio_url=mp3_url,
                    performer=artist,
                    audio_duration=duration,
                    caption=f'<a href="https://t.me/eschalon">eschalon</a>',
                    parse_mode="HTML"
                )
            )

        except Exception as e:
            print("Ошибка в inline audio:", e)
            continue

    await query.answer(results, cache_time=1, is_personal=True)