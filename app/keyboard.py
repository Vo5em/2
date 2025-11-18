from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from math import ceil

TRACKS_PER_PAGE = 10
MAX_TRACKS = 40


def build_tracks_keyboard(tracks: list, page: int = 1) -> InlineKeyboardBuilder:
    """
    Создаёт инлайн-клавиатуру с треками, разбивая их на страницы.
    """
    builder = InlineKeyboardBuilder()
    tracks = tracks[:MAX_TRACKS]

    total_pages = max(1, ceil(len(tracks) / TRACKS_PER_PAGE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * TRACKS_PER_PAGE
    end = start + TRACKS_PER_PAGE
    page_tracks = tracks[start:end]

    # 🎵 Кнопки треков - УБИРАЕМ ОБРЕЗАНИЕ ТЕКСТА
    for i, t in enumerate(page_tracks, start=start):
        # Формируем полное название как на втором скриншоте
        text = f"[{t['duration']}] {t['artist']} - {t['title']}"

        # НЕ ОБРЕЗАЕМ текст, а добавляем переносы
        formatted_text = add_line_breaks(text, max_line_length=40)

        builder.button(
            text=formatted_text,  # ПОЛНЫЙ текст с переносами
            callback_data=f"play_{i}"
        )

    builder.adjust(1)  # По одной кнопке в строке

    # 🔁 Кнопки навигации (как на втором скриншоте)
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ предыдущие", callback_data=f"page_{page - 1}"))

    nav_buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))

    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="следующие ➡️", callback_data=f"page_{page + 1}"))

    if nav_buttons:
        builder.row(*nav_buttons)

    return builder


def add_line_breaks(text: str, max_line_length: int = 40) -> str:
    """
    Добавляет переносы строк без обрезания текста.
    """
    if len(text) <= max_line_length:
        return text

    words = text.split()
    lines = []
    current_line = []

    for word in words:
        test_line = ' '.join(current_line + [word])
        if len(test_line) <= max_line_length:
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]

    if current_line:
        lines.append(' '.join(current_line))

    return '\n'.join(lines)