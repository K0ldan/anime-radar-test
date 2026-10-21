import html
import urllib.parse
from typing import Any

from src.models.anime import AiringSchedule, Media


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

    # Определение заголовка (обычная серия или финал сезона)
    ep_str = f"{schedule.episode}"
    if media.episodes:
        ep_str += f" из {media.episodes}"

    if media.episodes and schedule.episode >= media.episodes:
        header = f"🔥 <b>ФИНАЛ СЕЗОНА: {ep_str} серия!</b>"
    else:
        header = f"🎬 <b>Скоро выйдет {ep_str}-я серия!</b>"

    caption = (
        f"{header}\n"
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


def format_anime_catalog_card(
    anime: Media,
    status_label_override: str | None = None,
    header_prefix: str | None = None,
    footer_text: str | None = None,
) -> str:
    """
    Форматирует объект Media в стандартную карточку каталога аниме.
    Гарантирует 100% обратную совместимость с реверс-парсером callback_subscribe:
    - Сохраняет маркеры '🎬', '📌 Статус:', '⭐ Рейтинг:', '🏢 Студия:', '🏷 Жанры:'
    - Выполняет корректное экранирование HTML спецсимволов (&, <, >, ", ')
    - Предотвращает двойное экранирование
    """
    safe_title = html.escape(anime.display_title)
    score = (
        f"{anime.averageScore / 10:.1f} / 10"
        if anime.averageScore
        else "Без оценки"
    )
    genres = (
        html.escape(", ".join(anime.genres[:3])) if anime.genres else "Не указано"
    )
    studio = html.escape(anime.primary_studio)

    if status_label_override:
        status_label = html.escape(status_label_override)
        episodes_info = ""
    else:
        status_label = html.escape(anime.status_label)
        episodes_info = f" (всего серий: {anime.episodes})" if anime.episodes else ""

    card_body = (
        f"🎬 <b>{safe_title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Статус: <b>{status_label}</b>{episodes_info}\n"
        f"⭐ Рейтинг: <b>{score}</b>\n"
        f"🏢 Студия: {studio}\n"
        f"🏷 Жанры: {genres}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

    result = card_body
    if header_prefix:
        result = f"{header_prefix}\n{result}"
    if footer_text:
        result = f"{result}\n{footer_text}"

    return result
