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

        # 6. Data Retention Policy (Самоочистка старых логов > 90 дней)
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
