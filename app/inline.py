import aiohttp
import traceback
from aiogram import Router
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent, InputMediaAudio,InlineKeyboardMarkup,
    InlineKeyboardButton, ChosenInlineResult
)
from config import bot

from app.database.requests import (
    search_soundcloud, search_skysound,
    get_soundcloud_mp3_url, get_skysound_mp3,
    rank_tracks_by_similarity
)

router = Router()
TRACKS = {}


async def resolve_mp3_url(track):
    if track["source"] == "SoundCloud":
        return await get_soundcloud_mp3_url(track["url"])

    if track["source"] == "SkySound":
        return await get_skysound_mp3(track["url"])

    return None



async def probe_url(session, url, timeout=10):
    """HEAD + small GET probe; возвращает dict с info"""
    info = {"url": url, "head": None, "head_status": None, "content_type": None,
            "content_length": None, "get_status": None, "sample_bytes": None,
            "error": None}
    try:
        async with session.head(url, timeout=timeout, allow_redirects=True) as r:
            info["head_status"] = r.status
            info["head"] = dict(r.headers)
            info["content_type"] = r.headers.get("Content-Type")
            info["content_length"] = r.headers.get("Content-Length")
    except Exception as e_head:
        info["error"] = f"HEAD failed: {repr(e_head)}"
        # попробуем GET anyway
    # если HEAD прошёл и явно запрещает (например 403) — вернём
    try:
        # небольшой GET фрагмента (Range) — некоторые сервера не поддерживают, но пробуем
        headers = {"Range": "bytes=0-1023"}
        async with session.get(url, headers=headers, timeout=timeout, allow_redirects=True) as r2:
            info["get_status"] = r2.status
            info["get_headers"] = dict(r2.headers)
            chunk = await r2.content.read(1024)
            info["sample_bytes"] = len(chunk)
    except Exception as e_get:
        if info.get("error"):
            info["error"] += " | " + repr(e_get)
        else:
            info["error"] = f"GET failed: {repr(e_get)}"
    return info


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
    for i, t in enumerate(tracks[:18]):
        tid = f"{q.from_user.id}:{i}"
        TRACKS[tid] = t

        results.append(
            InlineQueryResultArticle(
                id=tid,
                title=f"{t['artist']} — {t['title']}",
                description=f"{t['source']} / {t['duration']}",
                thumb_url=t["thumb"],
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⏳ Загрузка…", callback_data="stub")]
                    ]
                ),
                input_message_content=InputTextMessageContent(
                    message_text="⏳ Загрузка трека…"
                )
            )
        )

    await q.answer(results, cache_time=0)


@router.chosen_inline_result()
async def diagnostic_chosen(result: ChosenInlineResult):
    print("\n===== CHOSEN_INLINE_RESULT (download → upload → edit) =====")

    tid = result.result_id
    track = TRACKS.get(tid)
    inline_id = result.inline_message_id
    user_id = result.from_user.id

    if not track:
        print("❌ Track not found")
        return

    print("✔ Track:", track)

    # --- 1. Получаем прямой mp3 URL ---
    try:
        if track.get("source") == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(track["url"])
        else:
            mp3_url = await get_skysound_mp3(track["url"])
    except Exception as e:
        print("❌ mp3 resolve error:", e)
        return

    if not mp3_url:
        await bot.edit_message_text(
            inline_message_id=inline_id,
            text="❌ Не удалось получить mp3 URL"
        )
        return

    print("✔ Resolved mp3:", mp3_url)

    # --- 2. Скачиваем mp3 ---
    import aiohttp
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(mp3_url) as r:
                if r.status != 200:
                    raise Exception(f"GET {r.status}")
                data = await r.read()
        except Exception as e:
            print("❌ Download failed:", e)
            await bot.edit_message_text(
                inline_message_id=inline_id,
                text="❌ Ошибка скачивания файла"
            )
            return

    print(f"✔ Downloaded {len(data)} bytes")

    # --- 3. Загружаем пользователю в личку ---
    try:
        sent = await bot.send_audio(
            chat_id=user_id,
            audio=data,
            title=f"{track['artist']} — {track['title']}",
            performer=track['artist'],
        )
        file_id = sent.audio.file_id
        print("✔ Uploaded. file_id:", file_id)
    except Exception as e:
        print("❌ Upload failed:", e)
        await bot.edit_message_text(
            inline_message_id=inline_id,
            text="❌ Ошибка отправки файла"
        )
        return

    # --- 4. Удаляем служебное сообщение ---
    try:
        await bot.delete_message(chat_id=user_id, message_id=sent.message_id)
    except Exception as e:
        print("⚠ Не удалось удалить личное сообщение:", e)

    # --- 5. Редактируем inline-сообщение ---
    try:
        await bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaAudio(
                media=file_id,
                title=f"{track['artist']} — {track['title']}",
                performer=track['artist']
            )
        )
        print("✔ Inline edited successfully")
    except Exception as e:
        print("❌ Inline edit failed:", e)
        await bot.edit_message_text(
            inline_message_id=inline_id,
            text=f"⚠ Ошибка редактирования. file_id: {file_id}"
        )

    print("===== END =====\n")



