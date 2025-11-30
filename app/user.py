from aiogram import Router, F
import aiohttp
import io
import tempfile
import re
import html
from aiogram.types import Message, CallbackQuery, BufferedInputFile, FSInputFile
from aiogram.filters import CommandStart, Command

from app.database.requests import set_user, search_skysound, search_soundcloud, rank_tracks_by_similarity
from app.database.requests import get_soundcloud_mp3_url
from app.keyboard import build_tracks_keyboard


user = Router()
user_tracks = {}

file_01 = "AgACAgIAAxkBAAIE52kgt3bMrOFh_E8zC13pEFXhAco9AALjEGsbdTMAAUlnAmO6fj4n1AEAAwIAA20AAzYE"
sticker01 = "CAACAgIAAxkBAAP-aSNrdHp8sYxEb5tu7MX9QeNe2BIAAoR3AAKBRPBIrSZeeRrV1yw2BA"
sticker02 = "CAACAgIAAxkBAAICaGkrit7X9qJNiots4pMh_1MoMmI2AAJ5hQACNFVgSVffCjgtzshbNgQ"

@user.message(CommandStart())
async def cmd_start(message: Message):
    await set_user(message.from_user.id)
    await message.answer('Добро пожаловать в музыкальный архив eschalon.\n\nЗапросите исполнителя или трек.')
    await message.answer_sticker(sticker=sticker02)


@user.message(F.text)
async def handle_message(message: Message):
    query = message.text.strip()
    status = await message.answer("подожди...")

    tracks = []
    tracks += await search_skysound(query)
    tracks += await search_soundcloud(query)

    if not tracks:
        await status.edit_text(f"«{query}» - ничего не найдено. Проверь правильность написания.")
        return

    # 🔍 Ранжируем по схожести
    tracks = rank_tracks_by_similarity(query, tracks)

    user_tracks[message.from_user.id] = tracks
    keyboard = build_tracks_keyboard(tracks, page=1)

    await status.edit_text(
        "Выберите трек из списка:",
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


    try:
        mp3_url = None

        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(url)
            if not mp3_url:
                await callback.message.edit_text("Не удалось получить mp3 =(")
                return

        else:
            # --- SkySound: ищем mp3 через регулярку на странице ---
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    html = await resp.text()
            mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            if not mp3_links:
                print(f"🚫 [SkySound] mp3 не найден")
                await callback.message.edit_text("Не удалось получить mp3  =(")
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

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        audio_file = FSInputFile(tmp_path, filename=f"{title}.mp3")
        ttumb = FSInputFile("ttumb.jpg")

        # --- Отправляем аудио ---
        await callback.message.delete()
        await callback.message.answer_audio(
            audio=audio_file,
            title=track['title'],
            performer=track['artist'],
            thumb=ttumb,
            caption= f'<a href="https://t.me/eschalon">eschalon</a>, <a href="t.me/eschalonmusicbot">music</a>',
            parse_mode="HTML"
        )
        await callback.message.answer_sticker(sticker=sticker01)


    except Exception as e:
        print(f"💥 Ошибка при отправке трека: {e}")
        await callback.message.answer("😔 Не удалось скачать трек.")


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

