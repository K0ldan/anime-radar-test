import html
import urllib.parse
from typing import Any

from src.models.anime import AiringSchedule


def format_telegram_card(schedule: AiringSchedule) -> tuple[str, str, dict[str, Any]]:
    """
    Форматирует серию в стильную Telegram-карточку с метаданными и кликабельными кнопками.
    Безопасно экранирует спецсимволы HTML для защиты от ошибок парсинга Telegram.
    Возвращает: (caption_html, photo_url, inline_reply_markup)
    """
    media = schedule.media
    dt_msk = schedule.release_datetime_msk

    date_str = dt_msk.strftime("%d.%m.%Y в %H:%M")
    countdown = schedule.countdown_str

    # Безопасное экранирование строк от стороннего API
    safe_title = html.escape(media.display_title)
    safe_studio = html.escape(media.primary_studio)
    genres_str = (
        html.escape(", ".join(media.genres[:3])) if media.genres else "Не указано"
    )

    caption = (
        f"🎬 <b>Скоро выйдет {schedule.episode}-я серия!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📺 <b>{safe_title}</b>\n\n"
        f"{media.rating_badge}\n\n"
        f"📅 <b>Релиз:</b> {date_str} (МСК)\n"
        f"{countdown}\n\n"
        f"🏢 <b>Студия:</b> {safe_studio}\n"
        f"🏷 <b>Жанры:</b> {genres_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

    photo_url = media.coverImage.best_quality

    anilist_url = media.siteUrl or f"https://anilist.co/anime/{media.id}"
    shikimori_search_url = (
        f"https://shikimori.one/animes?search={urllib.parse.quote(media.display_title)}"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "🌐 Открыть на AniList", "url": anilist_url},
                {"text": "🔍 Найти на Shikimori", "url": shikimori_search_url},
            ]
        ]
    }

    return caption, photo_url, reply_markup
