import re
import io
import os
import tempfile
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

TRACKS_TEMP = {}   # result_id -> track dict
MP3_CACHE = {}
THUMB_CACHE = {}

router = Router()

# таймауты и семафор
HTML_FETCH_TIMEOUT = 6
MP3_FETCH_TIMEOUT = 15
_fetch_sem = asyncio.Semaphore(6)


async def get_mp3(track: dict) -> str | None:
    """
    Универсальный get_mp3:
      - проверяет track['mp3']
      - пытает SoundCloud transcoding (если source содержит 'soundcloud')
      - пытает skysound parser (если source содержит 'skysound')
      - парсит HTML страницы на прямые .mp3 ссылки
      - делает быстрый HEAD/GET для проверки доступности
      - кэширует в MP3_CACHE
    Возвращает прямой mp3 URL или None.
    """
    if not track:
        return None

    url = track.get("url") or track.get("page") or track.get("link")
    source = (track.get("source") or "").lower()

    # 1) если уже есть готовый url в поле mp3
    if track.get("mp3"):
        return track["mp3"]

    # 2) кэш
    cache_key = url
    if cache_key and cache_key in MP3_CACHE:
        return MP3_CACHE[cache_key]

    try:
        # SoundCloud: обычно track["url"] это transcoding endpoint — попробуем получить прямой mp3
        if "soundcloud" in source or ("soundcloud.com" in (url or "")):
            try:
                mp3 = await asyncio.wait_for(get_soundcloud_mp3_url(url), timeout=MP3_FETCH_TIMEOUT)
            except Exception as e:
                mp3 = None
            if mp3:
                MP3_CACHE[cache_key] = mp3
                return mp3

        # SkySound: есть специализированный парсер у тебя (get_skysound_mp3)
        if "skysound" in source or "skysound7.com" in (url or ""):
            try:
                mp3 = await asyncio.wait_for(get_skysound_mp3(url), timeout=MP3_FETCH_TIMEOUT)
            except Exception:
                mp3 = None
            if mp3:
                MP3_CACHE[cache_key] = mp3
                return mp3

        # 3) Общий HTML поиск: найдем первые .mp3 ссылки на странице
        if url:
            timeout = aiohttp.ClientTimeout(total=HTML_FETCH_TIMEOUT)
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        # ищем mp3
                        m = re.findall(r'https?://[^\s"\'<>]+\.mp3', html)
                        if m:
                            # возьмем первый рабочий
                            candidate = m[0]
                            # проверим доступность (быстрый GET/HEAD)
                            try:
                                t2 = aiohttp.ClientTimeout(total=MP3_FETCH_TIMEOUT)
                                async with aiohttp.ClientSession(timeout=t2) as s2:
                                    async with s2.get(candidate, headers=headers) as r2:
                                        if r2.status == 200:
                                            MP3_CACHE[cache_key] = candidate
                                            return candidate
                            except Exception:
                                pass

    except asyncio.TimeoutError:
        return None
    except Exception as e:
        # логируем, но не падаем
        print("get_mp3() error:", e)
        return None

    return None


async def _download_bytes(url: str, timeout_sec=30) -> bytes | None:
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    print("download failed status", resp.status, "for", url)
                    return None
    except Exception as e:
        print("download_bytes error:", e)
        return None


@router.chosen_inline_result()
async def on_choose(res: ChosenInlineResult):
    """
    Handler: при выборе inline результата — пытаемся заменить placeholder на аудио.
    Логируем track и шаги. Пишем mp3 и thumb во временные файлы и отправляем через edit_message_media
    (если inline_message_id есть) или через send_audio в тот же чат (если inline_message_id отсутствует).
    """
    print("chosen_inline called:", res.result_id, "inline_msg_id:", res.inline_message_id)
    tid = res.result_id
    track = TRACKS_TEMP.get(tid)
    print("track content:", track)

    if not track:
        print("No track found for tid", tid)
        return

    # получаем mp3 URL (прямой)
    mp3_url = track.get("mp3") or await get_mp3(track)
    print("mp3_url:", mp3_url)
    if not mp3_url:
        # сообщим пользователю в том же inline-сообщении (если возможно)
        try:
            if res.inline_message_id:
                await res.bot.edit_message_text(inline_message_id=res.inline_message_id,
                                                text="❌ MP3 не найден.")
            else:
                # в личке/сохраненках: отправлять нельзя, поэтому молчим или логируем
                print("No inline_message_id and no mp3 -> abort")
        except Exception as e:
            print("Error while notifying about missing mp3:", e)
        return

    # скачиваем mp3 (bytes)
    audio_bytes = await _download_bytes(mp3_url, timeout_sec=MP3_FETCH_TIMEOUT)
    if not audio_bytes:
        print("Failed to download mp3 bytes")
        try:
            if res.inline_message_id:
                await res.bot.edit_message_text(inline_message_id=res.inline_message_id,
                                                text="❌ Не удалось скачать mp3.")
        except Exception as e:
            print("notify download fail", e)
        return

    # скачиваем обложку (если есть)
    thumb_bytes = None
    thumb_url = track.get("thumb") or track.get("artwork")
    if thumb_url:
        thumb_bytes = await _download_bytes(thumb_url, timeout_sec=10)

    # пишем во временные файлы (FSInputFile ожидает путь)
    tmp_mp3 = None
    tmp_thumb = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
            f.write(audio_bytes)
            tmp_mp3 = f.name

        if thumb_bytes:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f2:
                f2.write(thumb_bytes)
                tmp_thumb = f2.name

        # готовим FSInputFile из пути
        audio_input = FSInputFile(tmp_mp3)
        thumb_input = FSInputFile(tmp_thumb) if tmp_thumb else None

        # если есть inline_message_id — редактируем inline сообщение на аудио (работает в saved/чатах)
        if res.inline_message_id:
            try:
                await res.bot.edit_message_media(
                    inline_message_id=res.inline_message_id,
                    media=InputMediaAudio(
                        media=audio_input,
                        title=track.get("title"),
                        performer=track.get("artist"),
                        # в edit_message_media InputMediaAudio не всегда принимает thumbnail param;
                        # но при отправке file via FSInputFile telegram обычно использует thumb_input param name 'thumbnail'
                        # aiogram supports 'thumbnail' named param in SendAudio; for edit_message_media it passes file.
                        # We'll try to include caption that references author.
                        caption=f"{track.get('artist')} — {track.get('title')}"
                    )
                )
                print("Replaced inline message with audio (edit_message_media).")
            except Exception as e:
                print("edit_message_media failed:", e)
                # fallback: try to send audio to the chat where the inline was used (if available)
                try:
                    # If we have res.from_user.id, send to that chat (works only if bot can message)
                    await res.bot.send_audio(chat_id=res.from_user.id,
                                             audio=audio_input,
                                             performer=track.get("artist"),
                                             title=track.get("title"),
                                             thumbnail=thumb_input)
                    print("Sent audio to user by send_audio fallback.")
                except Exception as e2:
                    print("Fallback send_audio failed:", e2)
        else:
            # inline_message_id is None — send audio into the chat where user used inline (res.sender_chat?) or to user
            # chosen_inline gives from_user; cannot start PM if bot not allowed, but usually user invoked inline so we try send to from_user
            try:
                await res.bot.send_audio(chat_id=res.from_user.id,
                                         audio=audio_input,
                                         performer=track.get("artist"),
                                         title=track.get("title"),
                                         thumbnail=thumb_input)
                print("Sent audio to user via send_audio.")
            except Exception as e:
                print("send_audio to from_user failed:", e)
                # no more fallbacks
    finally:
        # cleanup temp files (try/except to ignore errors)
        if tmp_mp3:
            try: os.unlink(tmp_mp3)
            except: pass
        if tmp_thumb:
            try: os.unlink(tmp_thumb)
            except: pass

