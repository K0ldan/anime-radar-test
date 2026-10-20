# 🎬 Anime Radar — Event-Driven Data Platform & Notification Pipeline

[![Python](https://img.shields.io/badge/Python-3.12%20%7C%203.14-blue?logo=python)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Neon.tech-green?logo=postgresql)](https://neon.tech)
[![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-blue?logo=telegram)](https://telegram.org)
[![Sentry](https://img.shields.io/badge/Observability-Sentry.io-purple?logo=sentry)](https://sentry.io)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

Автоматизированный event-driven пайплайн данных и интерактивный Telegram-бот для персонального отслеживания выхода новых серий аниме в реальном времени с поддержкой рейтингов, таймеров релиза и полной дедупликацией.

100% бесплатная облачная инфраструктура с нулевыми затратами на содержание ($0/мес).

---

## 🏛️ Архитектура системы

```mermaid
flowchart TD
    subgraph Data Sources
        AL[AniList GraphQL API\n90 req/min Free]
    end

    subgraph Core Pipeline Engine
        Cron[GitHub Actions Cron / Worker] --> Ingest[State Diffing & Ingestion Engine]
        AL --> Ingest
        
        Ingest --> Deduplicate{Серия уже отправлялась?}
        Deduplicate -->|Нет| Dispatch[Personalized Dispatcher]
        Deduplicate -->|Да| Skip[Idempotent Skip]
        
        Dispatch --> TG[Telegram Notification with Poster & Buttons]
    end

    subgraph Storage Layer
        Dispatch --> Neon[(Neon Serverless PostgreSQL\nCatalog, Subscriptions & Deduplication Log)]
        Neon -.-> Deduplicate
    end

    subgraph Observability
        Ingest -.-> Sentry[Sentry.io Error Tracking]
        Dispatch -.-> Sentry
    end
```

---

## 🚀 Ключевые возможности

1. **GraphQL Ingestion:** Выгрузка комплексных структур данных (метаданные, таймстемпы, HD-постеры `extraLarge`, студии, жанры и оценки) за единый сетевой запрос.
2. **Строгие контракты данных (Data Contracts):** Полная типизация и валидация входящих данных на базе **Pydantic v2**.
3. **Идемпотентность и защита от дублей:** Контроль уникальности серий на уровне СУБД (`CONSTRAINT unique_anime_episode UNIQUE (anime_id, episode_number)`).
4. **Data Retention Policy:** Автоматическая самоочистка устаревших записей серий старше 90 дней (база никогда не переполняется).
5. **Интерактивный Telegram-бот (`aiogram`):**
   * Быстрый поиск аниме по тексту или команде `/track`.
   * Персональный список подписок (`/list`) с inline-кнопками удаления.
   * Ручная проверка расписания (`/check`).
6. **Production Observability:** Централизованный сбор ошибок, аномалий и контекста через **Sentry**.

---

## 🛠️ Стек технологий

* **Язык:** Python 3.12+ / 3.14
* **Сетевой клиент:** `httpx` (Async HTTP/2)
* **Telegram Framework:** `aiogram 3.x`
* **Валидация:** `pydantic v2`
* **База данных:** `asyncpg` + Neon PostgreSQL (Serverless Scale-to-Zero)
* **Observability:** `sentry-sdk`
* **CI/CD & Оркестрация:** GitHub Actions (Cron 24/7)

---

## ⚙️ Локальный запуск

1. **Клонируйте репозиторий:**
   ```bash
   git clone https://github.com/your-username/anime-radar.git
   cd anime-radar
   ```

2. **Создайте и активируйте виртуальное окружение:**
   ```bash
   python -m venv venv
   # Windows:
   .\venv\Scripts\activate
   # Linux/macOS:
   source venv/bin/activate
   ```

3. **Установите зависимости:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Настройте переменные окружения:**
   Скопируйте пример конфига:
   ```bash
   cp .env.example .env
   ```
   Заполните ваши ключи в `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   NEON_DATABASE_URL=postgresql://...
   SENTRY_DSN=https://...
   ```

5. **Запустите пайплайн проверки:**
   ```bash
   python main.py
   ```

6. **Запустите интерактивного бота:**
   ```bash
   python -m src.bot
   ```

---

## 📄 Лицензия

Проект распространяется под открытой лицензией [MIT](LICENSE).
