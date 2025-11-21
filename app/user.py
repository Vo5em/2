from aiogram import Router, F
import aiohttp
import io
import re
from aiogram.types import Message, CallbackQuery, BufferedInputFile, FSInputFile
from aiogram.filters import CommandStart, Command

from app.database.requests import set_user, search_skysound, search_soundcloud, rank_tracks_by_similarity
from app.database.requests import get_soundcloud_mp3_url
from app.keyboard import build_tracks_keyboard


user = Router()
user_tracks = {}



@user.message(CommandStart())
async def cmd_start(message: Message):
    await set_user(message.from_user.id)
    await message.answer('Добро пожаловать в бот!')



@user.message(F.text)
async def handle_message(message: Message):
    query = message.text.strip()
    await message.answer("🔍 Ищу треки, подожди...")

    tracks = []
    tracks += await search_skysound(query)
    tracks += await search_soundcloud(query)

    if not tracks:
        await message.answer("😔 Ничего не найдено.")
        return

    # 🔍 Ранжируем по схожести
    tracks = rank_tracks_by_similarity(query, tracks)

    user_tracks[message.from_user.id] = tracks
    keyboard = build_tracks_keyboard(tracks, page=1)

    await message.answer(
        "Выбери трек:",
        reply_markup=keyboard.as_markup()
    )

# ---------- Callback ----------
@user.callback_query(F.data.startswith("play_"))
async def play_track(callback: CallbackQuery):
    user_id = callback.from_user.id
    index = int(callback.data.split("_")[1])

    if user_id not in user_tracks or index >= len(user_tracks[user_id]):
        await callback.answer("⚠️ Трек не найден.")
        return

    track = user_tracks[user_id][index]
    url = track["url"]
    title = f"{track['artist']} — {track['title']}"

    await callback.message.answer(f"🎧 Загружаю: {title}")

    try:
        mp3_url = None

        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(url)
            if not mp3_url:
                await callback.message.answer("😔 Не удалось получить mp3")
                return

        else:
            # --- SkySound: ищем mp3 через регулярку на странице ---
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    html = await resp.text()
            mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            if not mp3_links:
                print(f"🚫 [SkySound] mp3 не найден")
                await callback.message.edit_text("😔 Не удалось получить mp3.")
                return
            mp3_url = mp3_links[0]

        # --- Скачиваем mp3 ---
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://soundcloud.com/" if track["source"] == "SoundCloud" else "https://skysound7.com/"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(mp3_url, headers=headers, timeout=30) as resp:
                if resp.status != 200:
                    print(f"⚠️ Ошибка загрузки mp3: {resp.status}")
                    await callback.message.edit_text("😔 Не удалось скачать трек (код ответа).")
                    return
                audio_bytes = await resp.read()

        # --- Проверяем размер ---
        if len(audio_bytes) < 50000:
            print("⚠️ mp3 слишком короткий, возможно битая ссылка.")
            await callback.message.edit_text("😔 Файл поврежден или недоступен.")
            return

        # --- Отправляем аудио ---
        audio_file = BufferedInputFile(audio_bytes, filename=f"{title}.mp3")
        await callback.message.delete()
        await callback.message.answer_audio(
            audio=audio_file,
            title=track['title'],
            performer=track['artist']
        )

    except Exception as e:
        print(f"💥 Ошибка при отправке трека: {e}")
        await callback.message.edit_text("😔 Не удалось скачать трек.")


@user.callback_query(lambda c: c.data.startswith("page_"))
async def handle_page_callback(callback_query: CallbackQuery):
    try:
        page = int(callback_query.data.split("_")[1])
    except Exception:
        print("⚠️ Ошибка парсинга номера страницы из callback_data:", callback_query.data)
        return

    user_id = callback_query.from_user.id
    if user_id not in user_tracks:
        await callback_query.answer("⚠️ Треки не найдены, попробуй поиск заново.", show_alert=True)
        return

    tracks = user_tracks[user_id]

    keyboard = build_tracks_keyboard(tracks, page)
    await callback_query.message.edit_reply_markup(reply_markup=keyboard.as_markup())

