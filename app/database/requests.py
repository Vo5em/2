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
    Получает рабочий mp3 URL с SoundCloud через transcoding API
    """
    full_url = f"{transcoding_url}?client_id={SOUNDCLOUD_CLIENT_ID}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/128.0 Safari/537.36",
        "Referer": "https://soundcloud.com/"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(full_url, headers=headers, proxy=proxy_url) as r:
                if r.status != 200:
                    print(f"⚠️ Ошибка запроса mp3 URL: {r.status}")
                    return None
                data = await r.json()
                return data.get("url")
    except Exception as e:
        print(f"💥 Ошибка получения mp3 URL: {e}")
        return None


async def search_soundcloud(query: str):
    print(f"\n🔎 [SoundCloud] Поиск запроса: '{query}'")
    url = f"https://api-v2.soundcloud.com/search/tracks?q={query}&client_id={SOUNDCLOUD_CLIENT_ID}&&limit=30"
    print(f"🌐 [SoundCloud] URL запроса: {url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, proxy=proxy_url) as r:
                if r.status != 200:
                    print(f"⚠️ [SoundCloud] Сервер вернул {r.status}")
                    text = await r.text()
                    print(f"🧾 Ответ (первые 500 символов): {text[:500]}")
                    return []

                data = await r.json()
    except Exception as e:
        print(f"💥 [SoundCloud] Ошибка: {e}")
        return []

    results = []
    collection = data.get("collection", [])
    print(f"🎶 Найдено треков: {len(collection)}")

    for item in collection[:30]:
        track_title = item.get("title", "Без названия")
        artist = item.get("user", {}).get("username", "Неизвестен")
        media = item.get("media", {})
        duration_ms = item.get("duration", 0)
        duration = round(duration_ms / 1000)
        duration_str = f"{duration // 60}:{duration % 60:02d}"

        # Берём transcoding URL, чтобы потом получить прямую mp3 ссылку
        mp3_transcoding_url = None
        for t in media.get("transcodings", []):
            preset = t.get("preset", "")
            format_protocol = t.get("format", {}).get("protocol", "")
            if "progressive" in preset or format_protocol == "progressive":
                mp3_transcoding_url = t["url"]
                break

        if not mp3_transcoding_url:
            continue

        results.append({
            "title": track_title,
            "artist": artist,
            "duration": duration_str,
            "url": mp3_transcoding_url,  # это ещё не mp3, а URL для получения mp3
            "source": "SoundCloud"
        })

    print(f"✅ Всего обработано треков: {len(results)}")
    return results

# --- SkySound поиск ---
seen_urls = set()

async def search_skysound(artist_query: str):
    """
    Самая надёжная версия поиска SkySound:
      ✓ корректный punycode
      ✓ отсутствие дублей
      ✓ стабильный парсинг длительности
      ✓ чистка мусорных названий
      ✓ глубокий поиск ссылок
    """

    # -------------------------------
    # 1) ЧИСТИМ НАЗВАНИЕ АРТИСТА
    # -------------------------------
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://skysound7.com/"
    }

    tracks = []
    seen = set()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=12) as resp:
                print("📡 Код:", resp.status)
                if resp.status != 200:
                    return []

                html = await resp.text()

    except Exception as e:
        print("❌ Ошибка соединения:", e)
        return []

    soup = BeautifulSoup(html, "html.parser")

    # -------------------------------
    # 2) ИЩЕМ ТРЕКИ
    # -------------------------------
    playlist_items = soup.select("div.playlist-item")

    if not playlist_items:
        print("🚫 playlist-item не найден")
        return []

    for item in playlist_items:

        # ссылка на трек
        link = item.find("a", href=True)
        if not link:
            continue

        href = link["href"].strip()

        # полный URL
        if not href.startswith("http"):
            href = f"https://{artist_domain}.skysound7.com{href}"

        if href in seen:
            continue
        seen.add(href)

        # -------------------------
        # НАЗВАНИЕ И АРТИСТ
        # -------------------------
        title_raw = (link.get("title") or link.text or "").strip()
        title_raw = re.sub(r"\b(скачать|download|слушать)\b", "", title_raw, flags=re.I)
        title_raw = title_raw.strip(" -\u2013\u2014")

        artist = ""
        title = title_raw

        if " - " in title_raw:
            artist, title = title_raw.split(" - ", 1)

        if not title:
            title = "Без названия"
        if not artist:
            artist = "Неизвестен"

        # -------------------------
        # ДЛИТЕЛЬНОСТЬ
        # -------------------------
        duration = "?:??"

        dur_block = item.select_one("div.playlist-right span.playlist-duration")
        if dur_block:
            duration = dur_block.text.strip()

        tracks.append({
            "title": title,
            "artist": artist,
            "url": href,
            "duration": duration,
            "source": "SkySound"
        })

    print(f"🎵 Найдено треков: {len(tracks)}")
    return tracks


async def get_skysound_mp3(track_page_url: str):
    """
    Надёжно получает mp3-ссылку со страницы SkySound.
    Ищет в HTML, в скриптах, проверяет валидность URL, делает HEAD-проверку.
    """
    print(f"\n🎯 [SkySound] Парсим страницу: {track_page_url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0 Safari/537.36"
        ),
        "Referer": track_page_url
    }

    async with aiohttp.ClientSession() as session:
        try:
            print("🌐 Загружаю страницу трека...")
            async with session.get(track_page_url, headers=headers, timeout=15) as resp:
                print(f"📡 Код ответа: {resp.status}")
                if resp.status != 200:
                    print("❌ Сервер вернул ошибку, прекращаю.")
                    return None

                html = await resp.text()
                print(f"📄 Загружено HTML: {len(html)} символов")

        except Exception as e:
            print(f"💥 Ошибка загрузки страницы: {type(e).__name__}: {e}")
            return None

    soup = BeautifulSoup(html, "html.parser")

    # -----------------------------------------
    # 1. ИЩЕМ ВСЕ ВОЗМОЖНЫЕ mp3-ССЫЛКИ
    # -----------------------------------------
    print("🔍 Ищу mp3 в HTML и JS...")

    mp3_candidates = set()

    # По регулярке (главный способ)
    mp3_candidates.update(re.findall(r'https:\/\/[^\s"]+\.mp3', html))

    # Из <audio> тегов
    for audio in soup.select("audio"):
        src = audio.get("src")
        if src and src.endswith(".mp3"):
            mp3_candidates.add(src)

    # Из data-* атрибутов
    for tag in soup.find_all():
        for attr, val in tag.attrs.items():
            if isinstance(val, str) and val.endswith(".mp3"):
                mp3_candidates.add(val)

    print(f"🎵 Найдено потенциальных mp3 ссылок: {len(mp3_candidates)}")
    for m in mp3_candidates:
        print(" ➤", m)

    if not mp3_candidates:
        print("🚫 Ни одной mp3 ссылки не найдено!")
        return None

    # -----------------------------------------
    # 2. ПРОВЕРКА КАЖДОЙ ССЫЛКИ (HEAD + GET)
    # -----------------------------------------
    async def check_mp3(url):
        """Проверяет что ссылка — настоящая mp3"""
        if not url.startswith("http"):
            # относительные пути
            try:
                base = track_page_url.split("/", 3)
                url = base[0] + "//" + base[2] + "/" + url.lstrip("/")
            except:
                return None

        print(f"\n🔎 Проверяю ссылку: {url}")

        try:
            # Сначала HEAD — быстро и не качает файл
            async with session.head(url, headers=headers, timeout=10, allow_redirects=True) as resp:
                ct = resp.headers.get("Content-Type", "")
                print(f"   HEAD: status={resp.status}, CT={ct}")

                if resp.status == 200 and "audio" in ct.lower():
                    print("   ✔ HEAD подтверждает mp3")
                    return url

            # Если HEAD ничего не дал — пробуем маленький GET
            async with session.get(url, headers=headers, timeout=15) as resp:
                ct = resp.headers.get("Content-Type", "")
                print(f"   GET: status={resp.status}, CT={ct}")

                if resp.status == 200 and "audio" in ct.lower():
                    print("   ✔ GET подтвердил mp3")
                    return url

        except Exception as e:
            print(f"   ✖ Ошибка при проверке ссылки: {type(e).__name__}: {e}")

        return None

    # -----------------------------------------
    # 3. Ищем первую РАБОЧУЮ ссылку
    # -----------------------------------------
    for candidate in mp3_candidates:
        valid = await check_mp3(candidate)
        if valid:
            print(f"\n✅ Найдена рабочая mp3: {valid}")
            return valid

    print("❌ Ни одна mp3-ссылка не работает")
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

