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
    print("\n===== DIAGNOSTIC CHOSEN_INLINE_RESULT =====")
    try:
        print("raw chosen result:", result.model_dump_json(indent=2))
    except Exception:
        print("raw chosen result (no json available)")

    tid = result.result_id
    print("result_id (tid):", tid)

    track = TRACKS.get(tid)
    if not track:
        print("❌ Track not found in TRACKS maps for tid:", tid)
        return

    print("✔ Found track:", track)

    inline_id = result.inline_message_id
    print("inline_message_id:", inline_id)

    user = getattr(result, "from_user", None)
    print("from_user:", user.model_dump() if user else None)

    # Resolve direct mp3 URL using your functions
    mp3_url = None
    try:
        if track.get("source") == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(track["url"])
        else:
            # for SkySound or others, prefer get_skysound_mp3 if available
            if "skysound" in track.get("source", "").lower():
                mp3_url = await get_skysound_mp3(track["url"])
            else:
                mp3_url = track.get("mp3") or track.get("url")
    except Exception as e:
        print("❌ Error resolving mp3 URL:", repr(e))
        traceback.print_exc()

    print("Resolved mp3_url:", mp3_url)

    if not mp3_url:
        print("❌ No mp3 URL — aborting.")
        # если есть inline_id, сообщим пользователю
        if inline_id:
            try:
                await bot.edit_message_text(
                    inline_message_id=inline_id,
                    text="❌ Не удалось получить прямой mp3 URL"
                )
            except Exception as e:
                print("edit_message_text error:", e)
        return

    # Проверим доступность URL с точки зрения Telegram (HEAD + small GET)
    async with aiohttp.ClientSession() as session:
        probe = await probe_url(session, mp3_url)
    print("Probe result for mp3_url:")
    for k, v in probe.items():
        print(f"  {k}: {v}")

    # ВАЖНО: Telegram запрещает некоторые MIME для inline edit -> нужно, чтобы Content-Type был audio/mpeg или audio/ogg и т.п.
    ct = probe.get("content_type") or (probe.get("get_headers") or {}).get("Content-Type")
    print("Detected content-type:", ct)

    audio = InputMediaAudio(
        media=mp3_url,  # прямая ссылка
        title=f"{track.get("artist")} — {track.get("title")}",  # красивое название
        performer=track.get("artist")
    )
    try:
        print("Attempting bot.edit_message_media(inline_message_id=..., media=InputMediaAudio(media=mp3_url))")
        await bot.edit_message_media(
            inline_message_id=inline_id,
            media=audio)

        print("✅ edit_message_media OK (no exception)")
        return
    except Exception as e:
        print("❌ edit_message_media raised:", repr(e))
        import traceback as _tb
        _tb.print_exc()

    # fallback: если edit failed, попробуем просто edit caption/text to show the mp3_url to user
    try:
        await bot.edit_message_text(
            inline_message_id=inline_id,
            text=f"⚠ Не удалось заменить на аудио. mp3 URL: {mp3_url}"
        )
    except Exception as e2:
        print("Fallback edit_message_text also failed:", repr(e2))

    print("===== END DIAGNOSTIC ====\n")

