import io
import re
import aiohttp
import asyncio
import traceback
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent,BufferedInputFile,InputMediaAudio, ChosenInlineResult
)
from aiogram.types.input_file import FSInputFile
from config import bot

from app.database.requests import search_soundcloud, search_skysound, get_soundcloud_mp3_url, get_skysound_mp3

router = Router()

# временный кэш
TRACKS_TEMP = {}   # result_id → full track dict
MP3_CACHE = {}


async def get_mp3(track):
    url = track.get("url")
    if not url:
        return None

    if url in MP3_CACHE:
        return MP3_CACHE[url]

    if track.get("source") == "soundcloud":
        mp3 = await get_soundcloud_mp3_url(url)
        if mp3:
            MP3_CACHE[url] = mp3
            return mp3

    if track.get("source") == "skysound":
        mp3 = await get_skysound_mp3(url)
        if mp3:
            MP3_CACHE[url] = mp3
            return mp3

    return None


# ----------------------- INLINE SEARCH -----------------------
@router.inline_query()
async def inline_search(q: InlineQuery):

    text = q.query.strip()
    if not text:
        return await q.answer([], cache_time=1)

    tracks = []
    tracks += await search_soundcloud(text)
    tracks += await search_skysound(text)

    results = []

    for i, t in enumerate(tracks[:30]):
        uid = f"trk_{i}"
        TRACKS_TEMP[uid] = t

        results.append(
            InlineQueryResultArticle(
                id=uid,
                title=f"{t['artist']} — {t['title']}",
                description=t.get("duration", ""),
                thumbnail_url=t.get("thumb"),
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "⏳ Загружаю аудио...\n\n"
                        f"🎵 {t['artist']} — {t['title']}"
                    )
                )
            )
        )

    await q.answer(results, cache_time=2)


# ----------------------- WHEN USER SELECTS A TRACK -----------------------
@router.chosen_inline_result()
async def chosen_inline(res: ChosenInlineResult):
    try:
        print("🔥 chosen_inline called:", res.result_id)
        track = TRACKS_TEMP.get(res.result_id)
        if not track:
            print("❌ track not found in TRACKS_TEMP for", res.result_id)
            return

        # 1) попробуем получить прямой mp3 URL (не скачиваем весь файл)
        mp3_url = track.get("mp3") or await get_mp3(track)  # get_mp3 должен возвращать URL или None
        print("mp3_url:", mp3_url)

        if not mp3_url:
            print("❌ no mp3_url available, abort")
            # опционально: покажем пользователю текст в inline сообщении
            if res.inline_message_id:
                await res.bot.edit_message_text(
                    inline_message_id=res.inline_message_id,
                    text="❌ MP3 не найден."
                )
            return

        # 2) Если есть inline_message_id — ПЫТАЕМСЯ заменить inline сообщение на аудио
        inline_id = getattr(res, "inline_message_id", None)
        if inline_id:
            print("inline_message_id present, trying edit_message_media with remote mp3 URL...")
            try:
                await res.bot.edit_message_media(
                    inline_message_id=inline_id,
                    media=InputMediaAudio(
                        media=mp3_url,           # <-- remote URL! (recommended)
                        title=track.get("title"),
                        performer=track.get("artist"),
                        caption=track.get("caption") or ""
                    )
                )
                print("✔ edit_message_media succeeded (remote mp3 URL).")
                return
            except Exception as e:
                print("❌ edit_message_media with remote URL failed:", e)
                # продолжим в fallback — попробуем отправить audio в chat (если разрешено)

        # 3) FALLBACK: отправка в чат где нажали (res.from_user or sender_chat)
        # Определяем chat_id: если chosen пришёл из конкретного чата, используем sender_chat.id (если есть)
        # но чаще всего нужно использовать res.from_user.id (тот, кто нажал)
        chat_id = None
        # sender_chat -- available when inline result was sent on behalf of a channel
        if getattr(res, "sender_chat", None):
            chat_id = res.sender_chat.id
        else:
            chat_id = res.from_user.id

        # Если бот не может писать пользователю в личку — отправка упадёт Forbidden.
        # Попробуем отправить send_audio напрямую с remote mp3_url (Telegram поддержит URL here too)
        try:
            # If you want to attach your own custom cover (stored locally as 'my_cover.jpg'):
            # thumb_file = FSInputFile("my_cover.jpg")  # uncomment to use your own cover file

            # If you want to keep remote thumb from track (but Telegram API expects InputFile for thumbnail),
            # you must download it to memory and pass as FSInputFile:
            thumb_input = None
            thumb_url = track.get("thumb")
            if thumb_url:
                try:
                    async with aiohttp.ClientSession() as sess:
                        async with sess.get(thumb_url, timeout=10) as r:
                            if r.status == 200:
                                b = await r.read()
                                bio = io.BytesIO(b)
                                bio.name = "cover.jpg"
                                thumb_input = FSInputFile(bio)
                except Exception as e:
                    print("⚠ failed to download thumb:", e)
                    thumb_input = None

            # Try to send remote mp3_url directly (Telegram will fetch it)
            await res.bot.send_audio(
                chat_id=chat_id,
                audio=mp3_url,   # remote URL is acceptable
                title=track.get("title"),
                performer=track.get("artist"),
                thumb=thumb_input  # FSInputFile or None
            )
            print("✔ send_audio succeeded (fallback path).")
            return
        except Exception as e:
            print("❌ send_audio fallback failed:", type(e), e)
            # If Forbidden, tell user politely (can't initiate conversation)
            if isinstance(e, aiogram.exceptions.TelegramForbiddenError):
                print("Forbidden: bot can't initiate conversation with this user/chat.")
                # If inline message existed, edit it with warning
                if inline_id:
                    try:
                        await res.bot.edit_message_text(
                            inline_message_id=inline_id,
                            text="❗ Бот не может отправить аудио в этот чат (Forbidden). Откройте бота в лс и нажмите /start."
                        )
                    except Exception:
                        pass
            else:
                # last resort: edit inline message to show error text
                if inline_id:
                    try:
                        await res.bot.edit_message_text(inline_message_id=inline_id, text="❌ Ошибка при отправке аудио.")
                    except Exception:
                        pass

    except Exception as outer_e:
        print("EXCEPTION in chosen_inline handler:", outer_e)
        traceback.print_exc()

