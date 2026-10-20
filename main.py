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
from src.formatters.telegram import format_telegram_card
from src.observability import init_sentry


async def send_telegram_card_to_chat(
    chat_id: int, caption: str, photo_url: str, reply_markup: dict
):
    """Отправляет карточку конкретному пользователю."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
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

        # 6. Умный еженедельный дайджест для пользователей с завершёнными тайтлами
        try:
            digest_users = await db.get_users_eligible_for_digest(days_interval=7)
            if digest_users:
                print(
                    f"☕ Найдено пользователей для еженедельного дайджеста: {len(digest_users)}"
                )
                for user_id in digest_users:
                    masked_uid = f"{str(user_id)[:4]}***"
                    fav_genres = await db.get_user_favorite_genres(user_id)
                    user_subs = await db.get_user_subscriptions(user_id)
                    exclude_ids = [s["anime_id"] for s in user_subs]
                    recs = await client.get_recommendations(
                        genres=fav_genres if fav_genres else None,
                        limit=2,
                        exclude_ids=exclude_ids,
                    )
                    if recs:
                        digest_header = (
                            "☕ <b>Ваш дайджест рекомендаций:</b>\n"
                            "Все тайтлы в вашей коллекции уже завершены. "
                            "По вашим любимым жанрам подобраны свежие онгоинги:"
                        )
                        anime = recs[0]
                        score = (
                            f"{anime.averageScore / 10:.1f} / 10"
                            if anime.averageScore
                            else "Без оценки"
                        )
                        genres_str = (
                            ", ".join(anime.genres[:3])
                            if anime.genres
                            else "Не указано"
                        )
                        caption = (
                            f"{digest_header}\n\n"
                            f"🎬 <b>{anime.display_title}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📌 Статус: <b>🟢 Выходит (Онгоинг)</b>\n"
                            f"⭐ Рейтинг: <b>{score}</b>\n"
                            f"🏢 Студия: {anime.primary_studio}\n"
                            f"🏷 Жанры: {genres_str}\n"
                            f"━━━━━━━━━━━━━━━━━━━━"
                        )
                        markup = {
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "➕ Отслеживать этот тайтл",
                                        "callback_data": f"sub:{anime.id}",
                                    },
                                    {
                                        "text": "🌐 Страница на AniList",
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
                                user_id, caption, anime.coverImage.best_quality, markup
                            )
                            await db.mark_user_digest_sent(user_id)
                            print(
                                f"   ✉️ Дайджест успешно отправлен пользователю [{masked_uid}]"
                            )
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            print(
                                f"   ❌ Ошибка отправки дайджеста пользователю [{masked_uid}]: {e}"
                            )
        except Exception as e:
            print(f"⚠️ Ошибка обработки дайджестов: {e}")

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
