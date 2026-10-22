import html
import urllib.parse
from datetime import datetime
from typing import Any

from src.models.anime import MSK_TIMEZONE, AiringSchedule, Media


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
        f"{anime.averageScore / 10:.1f} / 10" if anime.averageScore else "Без оценки"
    )
    genres = html.escape(", ".join(anime.genres[:3])) if anime.genres else "Не указано"
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


def format_weekly_schedule(
    schedules: list[AiringSchedule],
    now: datetime | None = None,
) -> str:
    """
    Форматирует расписание онгоингов на ближайшие 7 дней, сгруппированное по дням недели (МСК).
    """
    if now is None:
        now = datetime.now(MSK_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=MSK_TIMEZONE)

    now_ts = int(now.timestamp())
    end_ts = now_ts + 7 * 86400

    upcoming = [s for s in schedules if now_ts <= s.airingAt <= end_ts]
    if not upcoming:
        return (
            "📅 <b>Расписание на неделю:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "В ближайшие 7 дней новых серий ваших тайтлов не запланировано."
        )

    upcoming.sort(key=lambda s: s.airingAt)

    days_map: dict[str, list[AiringSchedule]] = {}
    day_names = {
        0: "Понедельник",
        1: "Вторник",
        2: "Среда",
        3: "Четверг",
        4: "Пятница",
        5: "Суббота",
        6: "Воскресенье",
    }
    month_names = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    }

    for s in upcoming:
        dt = s.release_datetime_msk
        day_str = f"🗓 <b>{dt.day} {month_names.get(dt.month, '')} ({day_names[dt.weekday()]})</b>"
        days_map.setdefault(day_str, []).append(s)

    lines = [
        "📅 <b>Ваша сетка онгоингов на неделю (время МСК):</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for day_header, items in days_map.items():
        lines.append(f"\n{day_header}")
        for s in items:
            time_str = s.release_datetime_msk.strftime("%H:%M")
            safe_title = html.escape(s.media.display_title)
            ep_info = f"{s.episode} сер."
            if s.media.episodes:
                ep_info += f" из {s.media.episodes}"
            lines.append(f"  • <b>{time_str}</b> — {safe_title} (<i>{ep_info}</i>)")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        "<i>🔔 Бот пришлёт уведомление с карточкой в момент выхода каждой серии!</i>"
    )
    return "\n".join(lines)
