import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEFAULT_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

from src.clients.anilist import AniListClient
from src.database import Database
from src.formatters.telegram import format_anime_catalog_card, format_telegram_card
from src.observability import init_sentry


async def send_telegram_card_to_chat(
    chat_id: int,
    caption: str,
    photo_url: str,
    reply_markup: dict,
    disable_notification: bool = False,
):
    """Отправляет карточку конкретному пользователю."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
        "disable_notification": disable_notification,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(url, json=payload)
        res.raise_for_status()


async def main():
    print("🚀 Запуск персонального пайплайна проверок релизов Anime Radar...")
    init_sentry("pipeline-worker")
    db = Database()
    client = AniListClient()

    try:
        await db.init_schema()

        # 1. Получаем список ID аниме, которые отслеживают наши пользователи
        tracked_ids = await db.get_all_tracked_anime_ids()

        if tracked_ids:
            print(
                f"🎯 Пользователи отслеживают тайтлов: {len(tracked_ids)} (IDs: {tracked_ids})"
            )
            schedules = await client.get_schedules_for_media_ids(tracked_ids)
        else:
            print(
                "💡 Список персональных подписок пока пуст. Запрашиваем общий ТОП расписания..."
            )
            schedules = await client.get_upcoming_schedules(
                limit=5, distinct_anime=True
            )

        print(f"📡 Получено серий для анализа: {len(schedules)}\n")
        new_notifications_count = 0

        # 2. Обрабатываем каждую серию с контролем идемпотентности
        for idx, schedule in enumerate(schedules, 1):
            anime_id = schedule.media.id
            ep_num = schedule.episode
            title = schedule.media.display_title

            # Проверяем в Neon: отправляли ли мы уже уведомление об этой серии?
            if await db.is_episode_processed(anime_id, ep_num):
                print(
                    f"[{idx}/{len(schedules)}] ⏭️ {title} (серия {ep_num}) — уже была отправлена ранее."
                )
                continue

            print(
                f"[{idx}/{len(schedules)}] 🆕 Найдена новая серия: {title} (серия {ep_num})!"
            )

            # 3. Находим адресатов: кто конкретно подписан на это аниме?
            subscribers = await db.get_subscribers_for_anime(anime_id)
            if not subscribers and DEFAULT_CHAT_ID:
                # Если аниме из общего топа, шлем администратору
                subscribers = [int(DEFAULT_CHAT_ID)]

            if not subscribers:
                print(f"   ⚠️ Нет подписчиков на тайтл {title}, пропускаем отправку.")
                continue

            caption, photo_url, reply_markup = format_telegram_card(schedule)

            # 4. Персональная рассылка подписчикам
            for sub_chat_id in subscribers:
                masked_id = f"{str(sub_chat_id)[:4]}***"
                try:
                    await send_telegram_card_to_chat(
                        sub_chat_id, caption, photo_url, reply_markup
                    )
                    print(f"   ✉️ Уведомление доставлено пользователю [{masked_id}]")
                    new_notifications_count += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"   ❌ Ошибка отправки пользователю [{masked_id}]: {e}")

            # 5. Фиксируем отправку серии в Neon (гарантия отсутствия дублей)
            await db.mark_episode_processed(anime_id, ep_num, schedule.airingAt)

        # 6. Радар эфира («Поймано в эфире» / Live Airing Match)
        # Для пользователей, у которых все тайтлы в коллекции завершены:
        # Проверяем, вышла ли свежая серия в их любимых жанрах, и отправляем TOP-1 без звука.
        try:
            live_users = await db.get_users_eligible_for_live_match(cooldown_hours=24)
            if live_users:
                print(f"📡 Пользователей для 'Радара эфира': {len(live_users)}")
                recent_airing = await client.get_recent_airing_schedules(
                    hours_back=24, limit=50
                )

                for user_id in live_users:
                    masked_uid = f"{str(user_id)[:4]}***"
                    fav_genres = await db.get_user_favorite_genres(user_id)
                    user_subs = await db.get_user_subscriptions(user_id)
                    exclude_ids = {s["anime_id"] for s in user_subs}

                    # Отбираем только не добавленные тайтлы с совпадением по жанрам
                    candidate_schedules = []
                    for s in recent_airing:
                        anime = s.media
                        if anime.id in exclude_ids:
                            continue
                        if fav_genres:
                            matched = any(g in fav_genres for g in anime.genres)
                            if not matched:
                                continue
                        candidate_schedules.append(s)

                    # Сортируем кандидатов по рейтингу (score desc)
                    candidate_schedules.sort(
                        key=lambda x: (
                            x.media.averageScore or 0,
                            x.media.popularity or 0,
                        ),
                        reverse=True,
                    )

                    chosen_schedule = (
                        candidate_schedules[0] if candidate_schedules else None
                    )

                    # Если в свежем эфире нет совпадений по жанрам, делаем фолбэк на топовую рекомендацию
                    if not chosen_schedule:
                        fallback_recs = await client.get_recommendations(
                            genres=fav_genres if fav_genres else None,
                            limit=1,
                            exclude_ids=list(exclude_ids),
                        )
                        if fallback_recs:
                            anime = fallback_recs[0]
                            ep_header = "⚡ <b>Свежий онгоинг в эфире</b>"
                        else:
                            continue
                    else:
                        anime = chosen_schedule.media
                        ep_header = f"⚡ <b>Только что вышла {chosen_schedule.episode} серия!</b>"

                    header_prefix = (
                        f"📡 <b>Радар поймал новинку в эфире!</b>\n\n{ep_header}"
                    )
                    footer_text = "<i>Все аниме в вашей коллекции завершены — начните новый онгоинг в один клик:</i>"
                    caption = format_anime_catalog_card(
                        anime,
                        status_label_override="🟢 Выходит (Онгоинг)",
                        header_prefix=header_prefix,
                        footer_text=footer_text,
                    )

                    markup = {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "➕ Добавить в отслеживание",
                                    "callback_data": f"sub:{anime.id}",
                                },
                                {
                                    "text": "🌐 AniList",
                                    "url": (
                                        anime.siteUrl
                                        or f"https://anilist.co/anime/{anime.id}"
                                    ),
                                },
                            ]
                        ]
                    }

                    try:
                        await send_telegram_card_to_chat(
                            user_id,
                            caption,
                            anime.coverImage.best_quality,
                            markup,
                            disable_notification=True,
                        )
                        await db.mark_user_live_match_sent(user_id)
                        print(
                            f"   📡 Карточка эфира доставлена пользователю [{masked_uid}]: {anime.display_title}"
                        )
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        print(
                            f"   ❌ Ошибка отправки карточки эфира [{masked_uid}]: {e}"
                        )
        except Exception as e:
            print(f"⚠️ Ошибка в блоке 'Радара эфира': {e}")

        # 7. Data Retention Policy (Самоочистка старых логов > 90 дней)
        try:
            deleted = await db.cleanup_old_records(days=90)
            if deleted > 0:
                print(f"🧹 Самоочистка: удалено записей старше 90 дней: {deleted}")
        except Exception as e:
            print(f"⚠️ Ошибка самоочистки: {e}")

        print(
            f"\n🏁 Пайплайн успешно завершен. Отправлено новых пушей: {new_notifications_count}"
        )
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
