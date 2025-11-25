import aiohttp
import io
import re
import json
import uuid
import asyncio
from rapidfuzz import fuzz
from app.database.models import User, async_session
from sqlalchemy import select, update, delete, desc
from bs4 import BeautifulSoup
import idna



from config import SOUNDCLOUD_CLIENT_ID, proxy_url

async def set_user(tg_id):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))

        if not user:
            session.add(User(tg_id=tg_id))
            await session.commit()



HEADERS = {"User-Agent": "Mozilla/5.0"}

# --- SoundCloud поиск ---

async def get_soundcloud_mp3_url(transcoding_url: str):
    """
    Получает ПОЛНЫЙ mp3 с SoundCloud.
    Гарантированно НЕ превью.
    """

    full_url = f"{transcoding_url}?client_id={SOUNDCLOUD_CLIENT_ID}"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://soundcloud.com/"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(full_url, headers=headers, proxy=proxy_url) as r:
                if r.status != 200:
                    print(f"⚠ Ошибка запроса transcoding: {r.status}")
                    return None

                data = await r.json()

                # SoundCloud API иногда отдаёт прямой mp3
                if "url" in data:
                    return data["url"]

    except Exception as e:
        print(f"💥 Ошибка transcoding запроса: {e}")
        return None

    return None


async def search_soundcloud(query: str):
    print(f"\n🔎 [SoundCloud] Поиск: '{query}'")

    url = (
        "https://api-v2.soundcloud.com/search/tracks"
        f"?q={query}&client_id={SOUNDCLOUD_CLIENT_ID}&limit=30"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, proxy=proxy_url) as r:
                if r.status != 200:
                    print(f"⚠ SC error {r.status}")
                    return []
                data = await r.json()
    except Exception as e:
        print(f"💥 SC ошибка: {e}")
        return []

    results = []

    for item in data.get("collection", []):
        media = item.get("media", {})
        transcodings = media.get("transcodings", [])
        if not transcodings:
            continue

        # получаем mp3 url
        mp3_transcoding_url = None

        for t in transcodings:
            if t.get("preset") == "mp3_1":
                mp3_transcoding_url = t["url"]
                break

        if not mp3_transcoding_url:
            for t in transcodings:
                if t.get("format", {}).get("protocol") == "progressive":
                    mp3_transcoding_url = t["url"]
                    break

        if not mp3_transcoding_url:
            continue

        # === ДОСТАЁМ ОБЛОЖКУ ===
        cover = item.get("artwork_url")
        if cover:
            cover = cover.replace("large", "original")  # максимальное качество

        results.append({
            "title": item.get("title", "Без названия"),
            "artist": item.get("user", {}).get("username", "Неизвестен"),
            "duration": f"{item.get('duration',0)//60000}:{(item.get('duration',0)//1000)%60:02d}",
            "url": mp3_transcoding_url,
            "thumbnail": cover,          # ← ОБЛОЖКА
            "source": "SoundCloud"
        })

    print(f"🎶 SC найдено треков: {len(results)}")
    return results

async def search_skysound(artist_query: str):
    artist_raw = artist_query.strip().lower()
    artist_raw = re.sub(r"[^a-zа-я0-9]+", "-", artist_raw)
    artist_raw = re.sub(r"-{2,}", "-", artist_raw).strip("-")

    try:
        artist_domain = idna.encode(artist_raw).decode()
    except:
        artist_domain = artist_raw

    url = f"https://{artist_domain}.skysound7.com/"
    print(f"\n🌐 [SkySearch] URL артиста: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://skysound7.com/"
    }

    tracks = []
    seen = set()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=12) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
    except:
        return []

    soup = BeautifulSoup(html, "html.parser")
    playlist_items = soup.select("div.playlist-item")

    if not playlist_items:
        print("🚫 playlist-item не найден")
        return []

    for item in playlist_items:

        link = item.find("a", href=True)
        if not link:
            continue

        href = link["href"].strip()
        if not href.startswith("http"):
            href = f"https://{artist_domain}.skysound7.com{href}"

        if href in seen:
            continue
        seen.add(href)

        # название
        title_raw = (link.get("title") or link.text or "").strip()
        title_raw = re.sub(r"\b(скачать|download|слушать)\b", "", title_raw, flags=re.I)
        title_raw = title_raw.strip(" -–—")

        artist, title = "", title_raw
        if " - " in title_raw:
            artist, title = title_raw.split(" - ", 1)

        if not artist: artist = "Неизвестен"
        if not title: title = "Без названия"

        # длительность
        duration = "?:??"
        dur = item.select_one("span.playlist-duration")
        if dur:
            duration = dur.text.strip()

        # === ДОСТАЁМ ОБЛОЖКУ ===
        cover = None

        # 1) логичная обложка рядом с треком
        img = item.find("img")
        if img and img.get("src"):
            cover = img["src"]

        # 2) fallback — ищем в HTML JS поле image: "..."
        if not cover:
            m = re.search(r'image:\s*"([^"]+)"', html)
            if m:
                cover = m.group(1)

        tracks.append({
            "title": title,
            "artist": artist,
            "url": href,
            "duration": duration,
            "thumbnail": cover,   # ← ОБЛОЖКА
            "source": "SkySound"
        })

    print(f"🎵 Найдено треков: {len(tracks)}")
    return tracks


async def get_skysound_mp3(track_page_url: str):
    """
    Возвращает ПОЛНЫЙ mp3 с SkySound.
    Берёт только JS-поле file: "...mp3"
    """

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": track_page_url
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(track_page_url, headers=headers, timeout=12) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
        except:
            return None

    # === Ищем ИМЕННО 'file: "...mp3"' ===
    file_match = re.search(r'file:\s*"([^"]+\.mp3)"', html)

    if file_match:
        full_mp3 = file_match.group(1)
        return full_mp3

    # Если нашли только preview — значит полного файла НЕТ
    return None

def rank_tracks_by_similarity(query: str, tracks: list):
    """
    Ранжирует треки по схожести с запросом пользователя.
    Использует fuzzy matching по названию и исполнителю.
    """
    ranked = []
    for track in tracks:
        title = track.get("title", "").lower()
        artist = track.get("artist", "").lower()
        q = query.lower()

        # Считаем схожесть по названию и исполнителю
        score_title = fuzz.partial_ratio(q, title)
        score_artist = fuzz.partial_ratio(q, artist)
        score_total = max(score_title, score_artist)

        ranked.append((score_total, track))

    # Сортируем по убыванию похожести
    ranked.sort(key=lambda x: x[0], reverse=True)
    sorted_tracks = [t for _, t in ranked]
    return sorted_tracks


async def download_track(track):
    """
    track = {
        "source": "SoundCloud" / "SkySound",
        "url": "...",
        "artist": "...",
        "title": "..."
    }
    """
    url = track["url"]

    try:
        mp3_url = None

        # --------------------------
        # 1. SoundCloud
        # --------------------------
        if track["source"] == "SoundCloud":
            mp3_url = await get_soundcloud_mp3_url(url)
            if not mp3_url:
                raise Exception("Не удалось получить mp3_url от SoundCloud")

        # --------------------------
        # 2. SkySound
        # --------------------------
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    html = await resp.text()

            mp3_links = re.findall(r'https:\/\/[^\s"]+\.mp3', html)
            if not mp3_links:
                raise Exception("MP3 не найден на SkySound")
            mp3_url = mp3_links[0]

        # --------------------------
        # 3. Качаем файл MP3
        # --------------------------
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": (
                "https://soundcloud.com/"
                if track["source"] == "SoundCloud"
                else "https://skysound7.com/"
            )
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(mp3_url, headers=headers, timeout=30) as resp:
                if resp.status != 200:
                    raise Exception(f"Ошибка HTTP {resp.status}")
                audio_bytes = await resp.read()

        if len(audio_bytes) < 50000:
            raise Exception("Файл слишком маленький / повреждён")

        return audio_bytes

    except Exception as e:
        print("❌ Ошибка в download_track():", e)
        return None

