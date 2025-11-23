from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup
from app.database.requests import search_skysound, search_soundcloud, rank_tracks_by_similarity

router = Router()

user_tracks = {}

@router.inline_query()
async def inline_search(inline_query: InlineQuery):
    query = inline_query.query.strip()

    if not query:
        return await inline_query.answer([])

    # Ищем треки
    tracks = []
    tracks += await search_skysound(query)
    tracks += await search_soundcloud(query)

    if not tracks:
        return await inline_query.answer(
            [],
            switch_pm_text="Ничего не найдено",
            switch_pm_parameter="start"
        )

    # Ранжируем
    tracks = rank_tracks_by_similarity(query, tracks)

    # Сохраняем в память под user_id
    user_tracks[inline_query.from_user.id] = tracks

    results = []
    for i, track in enumerate(tracks[:25]):
        title = f"{track['artist']} — {track['title']}"

        results.append(
            InlineQueryResultArticle(
                id=str(i),
                title=title,
                description=track["source"],
                input_message_content=InputTextMessageContent(
                    message_text=f"🎵 <b>{title}</b>\nЗагружаю...",
                    parse_mode="HTML"
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text="⬇️ Скачать",
                            callback_data=f"play_{i}"
                        )]
                    ]
                )
            )
        )

    await inline_query.answer(results, cache_time=1)