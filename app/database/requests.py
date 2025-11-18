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

'''async def check_artist_domain(session, artist: str):
    url = f"https://{artist.lower()}.skysound7.com/"
    try:
        async with session.get(url, headers=HEADERS, timeout=5) as resp:
            if resp.status == 200:
                return url
    except:
        return None
    return None'''

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
    """Парсит skysound7.com по имени артиста с чистыми названиями треков и длительностью"""
    artist_query_raw = artist_query.strip().lower()

    # 🔤 Заменяем пробелы и любые лишние символы (например, двойные дефисы)
    artist_query_raw = re.sub(r"[^a-zа-я0-9]+", "-", artist_query_raw)

    # 🧹 Убираем повторяющиеся дефисы и лишние в начале/конце
    artist_query_raw = re.sub(r"-{2,}", "-", artist_query_raw).strip("-")

    try:
        # 🔠 Конвертируем в punycode, если есть русские буквы
        artist_domain = idna.encode(artist_query_raw).decode()
    except idna.IDNAError:
        # Если домен на латинице — оставляем как есть
        artist_domain = artist_query_raw

    url = f"https://{artist_domain}.skysound7.com/"
    print(f"🌐 [SkySound] Формирую URL: {url}")

    tracks = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://skysound7.com/"
    }

    try:
        async with aiohttp.ClientSession() as session:
            print("🔗 [SkySound] Отправляю запрос...")
            async with session.get(url, headers=headers, timeout=10) as resp:
                print(f"📡 [SkySound] Код ответа: {resp.status}")
                if resp.status != 200:
                    print("⚠️ [SkySound] Сервер вернул не 200 — страница недоступна.")
                    return []

                html = await resp.text()
                print(f"📃 [SkySound] Длина HTML: {len(html)} символов")

                if "Not Found" in html or "404" in html:
                    print("🚫 [SkySound] На странице 'Not Found'")
                    return []

                soup = BeautifulSoup(html, "html.parser")
                print("🔍 [SkySound] Ищу ссылки на треки...")

                links = soup.select("a[href*='/t/']")
                print(f"🎶 [SkySound] Найдено ссылок: {len(links)}")


                for link in links:
                    href = link.get("href")
                    if not href:
                        continue
                    if not href.startswith("http"):
                        href = f"https://{artist_domain}.skysound7.com{href}"


                    # ⏱️ Пробуем найти длительность (в формате 3:42)
                    track_container = link.find_parent("div", class_="playlist-item") or link.parent

                    duration = "?:??"
                    if track_container:
                        # ищем соседний блок с длительностью
                        playlist_right = track_container.find_next("div", class_="playlist-right")
                        if playlist_right:
                            duration_tag = playlist_right.find("span", class_="playlist-duration")
                            if duration_tag:
                                duration = duration_tag.text.strip()

                    print("⏱ Длительность:", duration)


                    # 🔁 Пропускаем дубликаты по ссылке
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    # 🎵 Получаем текст и чистим
                    title_raw = (link.get("title") or link.text or "").strip()
                    title_raw = re.sub(r"\bскачать\b", "", title_raw, flags=re.IGNORECASE)
                    title_raw = re.sub(r"^\s*[\-–—‒−]+\s*", "", title_raw).strip()

                    # 🎤 Разделяем артист и трек (если есть "-")
                    if " - " in title_raw:
                        artist, title = title_raw.split(" - ", 1)
                    else:
                        artist, title = "", title_raw



                    tracks.append({
                        "title": title or "Без названия",
                        "artist": artist or "Неизвестен",
                        "url": href,
                        "duration": duration,
                        "source": "SkySound"
                    })


    except aiohttp.ClientError as e:
        print(f"❌ [SkySound] Ошибка соединения: {e}")
    except Exception as e:
        print(f"💥 [SkySound] Неожиданная ошибка: {e}")

    print(f"✅ [SkySound] Всего найдено треков: {len(tracks)}")
    return tracks


async def get_skysound_mp3(track_page_url: str):
    """Извлекает прямую mp3-ссылку со страницы SkySound (расширенный лог)"""
    print(f"\n🎯 [SkySound] Парсим страницу: {track_page_url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/128.0 Safari/537.36"
    }

    try:
        async with aiohttp.ClientSession() as session:
            print("🌐 Отправляю запрос к странице трека...")
            async with session.get(track_page_url, headers=headers, timeout=15) as resp:
                print(f"📡 Код ответа: {resp.status}")
                html = await resp.text()
                print(f"📄 Размер HTML: {len(html)} символов")

        soup = BeautifulSoup(html, "html.parser")

        # Ищем аудио или ссылку на mp3
        print("🔍 [SkySound] Ищу mp3 через регулярку...")
        mp3_pattern = re.compile(r'https:\/\/[^\s"]+\.mp3')
        matches = mp3_pattern.findall(html)
        if matches:
            print(f"🎯 Найдено потенциальных ссылок: {len(matches)}")
            for i, m in enumerate(matches[:5]):
                print(f"🔗 {i + 1}. {m}")
            mp3_url = matches[0]
        else:
            print("🚫 mp3 не найден даже по регулярке.")
            preview = html[:600]
            print(f"🧾 Превью HTML:\n{preview}")
            return None



    except Exception as e:
        print(f"💥 Ошибка в get_skysound_mp3: {type(e).__name__}: {e}")
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

