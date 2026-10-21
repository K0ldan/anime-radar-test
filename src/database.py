import os
import re
import sys
from datetime import UTC

import asyncpg
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

DATABASE_URL = os.getenv("NEON_DATABASE_URL")

CREATE_TABLES_SQL = """
-- 1. Таблица пользователей
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Таблица отслеживаемых тайтлов (каталог аниме)
CREATE TABLE IF NOT EXISTS tracked_anime (
    anime_id INTEGER PRIMARY KEY,
    title VARCHAR(500) NOT NULL,
    site_url VARCHAR(500),
    cover_image_url VARCHAR(500),
    status VARCHAR(50),
    total_episodes INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

ALTER TABLE tracked_anime ADD COLUMN IF NOT EXISTS status VARCHAR(50);
ALTER TABLE tracked_anime ADD COLUMN IF NOT EXISTS total_episodes INTEGER;
ALTER TABLE tracked_anime ADD COLUMN IF NOT EXISTS genres TEXT[];
ALTER TABLE tracked_anime ADD COLUMN IF NOT EXISTS studio VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_digest_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_live_match_at TIMESTAMP WITH TIME ZONE;

-- 3. Персональные подписки пользователей
CREATE TABLE IF NOT EXISTS user_subscriptions (
    telegram_chat_id BIGINT NOT NULL,
    anime_id INTEGER REFERENCES tracked_anime(anime_id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (telegram_chat_id, anime_id)
);

-- 4. Идемпотентный лог обработанных серий (защита от повторных пушей)
CREATE TABLE IF NOT EXISTS processed_episodes (
    id SERIAL PRIMARY KEY,
    anime_id INTEGER NOT NULL,
    episode_number INTEGER NOT NULL,
    airing_at TIMESTAMP WITH TIME ZONE,
    notified_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_anime_episode UNIQUE (anime_id, episode_number)
);

CREATE INDEX IF NOT EXISTS idx_processed_episodes_lookup 
ON processed_episodes (anime_id, episode_number);
"""


def clean_postgres_url(raw_url: str | None) -> str:
    """Безопасная очистка URL от неподдерживаемых asyncpg параметров."""
    if not raw_url:
        return ""
    cleaned = re.sub(r"[?&]channel_binding=[^&]+", "", raw_url)
    if "?" not in cleaned and "&" in cleaned:
        cleaned = cleaned.replace("&", "?", 1)
    return cleaned


class Database:
    def __init__(self, db_url: str | None = None):
        self.db_url = clean_postgres_url(db_url or DATABASE_URL)
        self._pool: asyncpg.Pool | None = None

    async def get_pool(self) -> asyncpg.Pool:
        """Пул соединений для высокой производительности и надежности."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.db_url, min_size=1, max_size=5, ssl="require", command_timeout=15.0
            )
        return self._pool

    async def close(self):
        """Закрытие пула соединений."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def init_schema(self):
        """Создает таблицы и индексы в базе данных (идемпотентно)."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(CREATE_TABLES_SQL)

    async def register_user(self, telegram_chat_id: int, username: str | None = None):
        """Регистрирует пользователя (использует параметризованный запрос - защита от SQLi)."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (telegram_chat_id, username)
                VALUES ($1, $2)
                ON CONFLICT (telegram_chat_id) DO UPDATE 
                SET username = EXCLUDED.username;
            """,
                telegram_chat_id,
                username,
            )

    async def add_subscription(
        self,
        telegram_chat_id: int,
        anime_id: int,
        title: str,
        site_url: str,
        cover_image_url: str,
        status: str | None = None,
        total_episodes: int | None = None,
        genres: list[str] | None = None,
        studio: str | None = None,
    ):
        """Добавляет аниме в каталог и оформляет подписку."""
        pool = await self.get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO tracked_anime (anime_id, title, site_url, cover_image_url, status, total_episodes, genres, studio)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (anime_id) DO UPDATE 
                SET title = EXCLUDED.title, site_url = EXCLUDED.site_url, cover_image_url = EXCLUDED.cover_image_url,
                    status = COALESCE(EXCLUDED.status, tracked_anime.status),
                    total_episodes = COALESCE(EXCLUDED.total_episodes, tracked_anime.total_episodes),
                    genres = COALESCE(EXCLUDED.genres, tracked_anime.genres),
                    studio = COALESCE(EXCLUDED.studio, tracked_anime.studio);
            """,
                anime_id,
                title,
                site_url,
                cover_image_url,
                status,
                total_episodes,
                genres,
                studio,
            )

            await conn.execute(
                """
                INSERT INTO user_subscriptions (telegram_chat_id, anime_id)
                VALUES ($1, $2)
                ON CONFLICT (telegram_chat_id, anime_id) DO NOTHING;
            """,
                telegram_chat_id,
                anime_id,
            )

    async def remove_subscription(self, telegram_chat_id: int, anime_id: int):
        """Удаляет подписку пользователя на тайтл."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM user_subscriptions 
                WHERE telegram_chat_id = $1 AND anime_id = $2;
            """,
                telegram_chat_id,
                anime_id,
            )

    async def get_user_subscriptions(self, telegram_chat_id: int) -> list[dict]:
        """Возвращает список отслеживаемых аниме для пользователя."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.anime_id, t.title, t.site_url, t.cover_image_url, t.status, t.total_episodes, s.created_at
                FROM user_subscriptions s
                JOIN tracked_anime t ON s.anime_id = t.anime_id
                WHERE s.telegram_chat_id = $1
                ORDER BY s.created_at DESC;
            """,
                telegram_chat_id,
            )
            return [dict(r) for r in rows]

    async def get_subscribers_for_anime(self, anime_id: int) -> list[int]:
        """Возвращает список chat_id пользователей, подписанных на аниме."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT telegram_chat_id FROM user_subscriptions
                WHERE anime_id = $1;
            """,
                anime_id,
            )
            return [r["telegram_chat_id"] for r in rows]

    async def get_all_tracked_anime_ids(self) -> list[int]:
        """Возвращает список всех ID аниме, которые отслеживает хотя бы один пользователь."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT anime_id FROM user_subscriptions;
            """)
            return [r["anime_id"] for r in rows]

    async def is_episode_processed(self, anime_id: int, episode_number: int) -> bool:
        """Проверяет, отправляли ли мы уже эту серию."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                """
                SELECT 1 FROM processed_episodes 
                WHERE anime_id = $1 AND episode_number = $2;
            """,
                anime_id,
                episode_number,
            )
            return val is not None

    async def mark_episode_processed(
        self, anime_id: int, episode_number: int, airing_at: int | None = None
    ):
        """Фиксирует факт отправки серии в лог (дедупликация)."""
        from datetime import datetime

        airing_dt = datetime.fromtimestamp(airing_at, tz=UTC) if airing_at else None

        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO processed_episodes (anime_id, episode_number, airing_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (anime_id, episode_number) DO NOTHING;
            """,
                anime_id,
                episode_number,
                airing_dt,
            )

    async def cleanup_old_records(self, days: int = 90) -> int:
        """Data Retention: удаляет серии старше N дней."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            res = await conn.execute(
                """
                DELETE FROM processed_episodes
                WHERE notified_at < NOW() - ($1 || ' days')::INTERVAL;
            """,
                str(days),
            )
            count = int(res.split(" ")[1]) if "DELETE" in res else 0
            return count

    async def get_user_favorite_genres(self, telegram_chat_id: int) -> list[str]:
        """Возвращает список любимых жанров пользователя на основе его коллекции."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT unnest(t.genres) as genre, COUNT(*) as freq
                FROM user_subscriptions s
                JOIN tracked_anime t ON s.anime_id = t.anime_id
                WHERE s.telegram_chat_id = $1 AND t.genres IS NOT NULL
                GROUP BY genre
                ORDER BY freq DESC, genre
                LIMIT 5;
            """,
                telegram_chat_id,
            )
            return [r["genre"] for r in rows]

    async def get_users_eligible_for_live_match(
        self, cooldown_hours: int = 24
    ) -> list[int]:
        """
        Находит пользователей для отправки карточки «Поймано в эфире» (Live Airing Match):
        1. Имеют хотя бы 1 сохранённый тайтл.
        2. Все тайтлы в списке уже FINISHED (нет активных онгоингов).
        3. Карточка эфира не отправлялась последние `cooldown_hours` часов (или ни разу).
        """
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT u.telegram_chat_id
                FROM users u
                WHERE EXISTS (
                    SELECT 1 FROM user_subscriptions s WHERE s.telegram_chat_id = u.telegram_chat_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM user_subscriptions s
                    JOIN tracked_anime t ON s.anime_id = t.anime_id
                    WHERE s.telegram_chat_id = u.telegram_chat_id AND (t.status = 'RELEASING' OR t.status IS NULL)
                )
                AND (u.last_live_match_at IS NULL OR u.last_live_match_at < NOW() - ($1 || ' hours')::INTERVAL);
            """,
                str(cooldown_hours),
            )
            return [r["telegram_chat_id"] for r in rows]

    async def mark_user_live_match_sent(self, telegram_chat_id: int):
        """Обновляет таймстемп отправки live match пользователю."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users SET last_live_match_at = NOW() WHERE telegram_chat_id = $1;
            """,
                telegram_chat_id,
            )

    async def get_user_profile(self, telegram_chat_id: int) -> dict:
        """
        Возвращает агрегированные данные аналитики профиля зрителя:
        - дата регистрации и никнейм
        - список всех подписок с жанрами, статусами и студиями
        """
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT created_at, username FROM users WHERE telegram_chat_id = $1;",
                telegram_chat_id,
            )
            subs = await conn.fetch(
                """
                SELECT t.anime_id, t.title, t.status, t.total_episodes, t.genres, t.studio
                FROM user_subscriptions s
                JOIN tracked_anime t ON s.anime_id = t.anime_id
                WHERE s.telegram_chat_id = $1;
            """,
                telegram_chat_id,
            )

        return {
            "username": user_row["username"] if user_row else None,
            "created_at": user_row["created_at"] if user_row else None,
            "subscriptions": [dict(r) for r in subs],
        }
