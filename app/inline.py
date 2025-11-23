import uuid
import re
import tempfile
import aiohttp
from aiogram import Router, F
from aiogram.types import (
    InlineQuery,
    FSInputFile,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
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
        # Показываем подсказку, пока пользователь ничего не ввёл
        await query.answer(
            results=[],
            switch_pm_text="Введите название трека",
            switch_pm_parameter="start",
            cache_time=1
        )
        return

    # Ищем треки
    tracks = []
    tracks += await search_skysound(text)
    tracks += await search_soundcloud(text)

    if not tracks:
        await query.answer([], cache_time=1)
        return

    # Ранжируем
    tracks = rank_tracks_by_similarity(text, tracks)

    results = []
    for idx, track in enumerate(tracks[:20]):
        title = f"{track['artist']} — {track['title']}"

        # В инлайне нельзя сразу отправить аудио, поэтому создаём "заглушку" = сообщение, которое бот перезапишет.
        results.append(
            InlineQueryResultArticle(
                id=str(idx),
                title=title,
                description=f"⏱ {track['duration']}",
                input_message_content=InputTextMessageContent(
                    message_text=f"🔄 Загружаю: {title}..."
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⏬ Загрузка...", callback_data=f"inline_play_{idx}")]
                    ]
                )
            )
        )

    await query.answer(results, cache_time=1)


@router.callback_query(F.data.startswith("inline_play_"))
async def inline_play(callback: CallbackQuery):
    user_id = callback.from_user.id
    index = int(callback.data.split("_")[2])

    # Достаём треки, как в обычной версии
    # Для инлайна они должны лежать глобально
    if user_id not in user_tracks or index >= len(user_tracks[user_id]):
        await callback.answer("Трек не найден.", show_alert=True)
        return

    track = user_tracks[user_id][index]
    url = track["url"]
    title = f"{track['artist']} — {track['title']}"

    await callback.message.edit_text(f"🔄 Загружаю {title}...")

    try:
        # =======================
        #  СКАЧИВАНИЕ MP3 (1:1)
        # =======================
        mp3_url = None

        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(url)
            if not mp3_url:
                await callback.message.edit_text("Не удалось получить mp3 =(")
                return
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    html = await resp.text()
            mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            if not mp3_links:
                await callback.message.edit_text("Не удалось получить mp3 =(")
                return
            mp3_url = mp3_links[0]

        # Скачиваем файл
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://soundcloud.com/" if track["source"] == "SoundCloud" else "https://skysound7.com/"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(mp3_url, headers=headers, timeout=30) as resp:
                if resp.status != 200:
                    await callback.message.edit_text("Ошибка загрузки трека.")
                    return
                audio_bytes = await resp.read()

        if len(audio_bytes) < 50000:
            await callback.message.edit_text("Файл повреждён.")
            return

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        audio_file = FSInputFile(tmp_path, filename=f"{title}.mp3")
        thumb = FSInputFile("ttumb.jpg")

        # =======================
        #  ОТПРАВКА MP3 (как в play_track)
        # =======================
        await callback.message.delete()
        await callback.message.answer_audio(
            audio=audio_file,
            title=track['title'],
            performer=track['artist'],
            thumb=thumb,
            caption=f'<a href="https://t.me/eschalon">eschalon</a>, <a href="t.me/eschalonmusicbot">music</a>',
            parse_mode="HTML"
        )

    except Exception as e:
        print("Ошибка inline:", e)
        await callback.message.edit_text("Не удалось скачать трек.")