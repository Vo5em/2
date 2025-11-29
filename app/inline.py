import aiohttp
import traceback
import tempfile
import os
from mutagen.id3 import ID3, APIC, TIT2, TPE1, ID3NoHeaderError
from mutagen.mp3 import MP3
from aiogram import Router
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    FSInputFile,BufferedInputFile,
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
                title=f"{t['title']}",
                description=f"{t['artist']} / {t['duration']}",
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
    print("\n===== CHOSEN_INLINE_RESULT (download → embed_cover → upload → edit) =====")

    tid = result.result_id
    track = TRACKS.get(tid)
    inline_id = result.inline_message_id
    user_id = result.from_user.id

    if not track:
        print("❌ Track not found")
        return

    print("✔ Track:", track)

    # 1) Resolve mp3 url
    try:
        if track.get("source") == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(track["url"])
        else:
            mp3_url = await get_skysound_mp3(track["url"])
    except Exception as e:
        print("❌ mp3 resolve error:", e)
        return

    if not mp3_url:
        await bot.edit_message_text(inline_message_id=inline_id, text="❌ Не удалось получить mp3 URL")
        return

    print("✔ Resolved mp3:", mp3_url)

    # 2) Download mp3
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(mp3_url, timeout=30) as r:
                if r.status != 200:
                    raise Exception(f"GET {r.status}")
                mp3_bytes = await r.read()
        except Exception as e:
            print("❌ Download failed:", e)
            await bot.edit_message_text(inline_message_id=inline_id, text="❌ Ошибка скачивания файла")
            return

    print(f"✔ Downloaded {len(mp3_bytes)} bytes")

    # 3) Embed cover (safe)
    try:
        mp3_ready = embed_cover(
            mp3_bytes,
            "ttumb.jpg",
            title=track.get("title", ""),
            artist=track.get("artist", "")
        )
    except Exception as e:
        print("❌ embed_cover failed:", e)
        traceback.print_exc()
        # fallback: если ошибка — используем оригинальные mp3_bytes
        mp3_ready = mp3_bytes

    print(f"✔ MP3 with cover ready ({len(mp3_ready)} bytes)")

    # 4) Upload to user (BufferedInputFile)
    audio_file = BufferedInputFile(
        mp3_ready,
        filename=f"{track.get('artist','unknown')} - {track.get('title','track')}.mp3"
    )

    try:
        sent = await bot.send_audio(
            chat_id=user_id,
            audio=audio_file,
            title=track.get("title"),
            performer=track.get("artist"),
            disable_notification=True
        )
        file_id = sent.audio.file_id
        print("✔ Uploaded OK. file_id:", file_id)
    except Exception as e:
        print("❌ Upload failed:", e)
        traceback.print_exc()
        await bot.edit_message_text(inline_message_id=inline_id, text="❌ Ошибка отправки файла")
        return

    # 5) Remove helper message in user's private chat
    try:
        await bot.delete_message(chat_id=user_id, message_id=sent.message_id)
    except Exception as e:
        print("⚠ Не удалось удалить личное сообщение:", e)

    # 6) Edit inline message to use file_id
    try:
        await bot.edit_message_media(
            inline_message_id=inline_id,
            media=InputMediaAudio(
                media=file_id,
                title=track.get("title"),
                performer=track.get("artist")
            )
        )
        print("✔ Inline edited successfully")
    except Exception as e:
        print("❌ Inline edit failed:", e)
        traceback.print_exc()
        await bot.edit_message_text(inline_message_id=inline_id, text=f"⚠ Ошибка редактирования. file_id: {file_id}")

    print("===== END =====\n")


def embed_cover(mp3_bytes: bytes, cover_path: str, title: str, artist: str) -> bytes:
    """
    Надёжно встраивает JPEG cover_path в mp3_bytes (APIC) и возвращает новые байты mp3.
    Работает через временный файл, чтобы mutagen корректно сохранил аудио + теги.
    """
    # 1) записать mp3_bytes во временный файл
    tmp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    try:
        tmp_mp3.write(mp3_bytes)
        tmp_mp3.close()

        # 2) убедиться, что есть ID3 (создать, если нет)
        try:
            tags = ID3(tmp_mp3.name)
        except ID3NoHeaderError:
            tags = ID3()

        # 3) прочитать обложку
        with open(cover_path, "rb") as f:
            cover_data = f.read()

        # 4) вписать APIC + TIT2 + TPE1
        tags.delall("APIC")
        tags.add(APIC(
            encoding=3,          # utf-8
            mime='image/jpeg',
            type=3,              # front cover
            desc='Cover',
            data=cover_data
        ))
        tags.delall("TIT2")
        tags.add(TIT2(encoding=3, text=title))
        tags.delall("TPE1")
        tags.add(TPE1(encoding=3, text=artist))

        # 5) сохранить теги в файл (mutagen позаботится о сохранении аудио)
        tags.save(tmp_mp3.name)

        # 6) прочитать получившийся файл
        with open(tmp_mp3.name, "rb") as f:
            result = f.read()

    finally:
        try:
            os.unlink(tmp_mp3.name)
        except Exception:
            pass

    return result

