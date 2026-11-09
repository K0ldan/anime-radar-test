import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

# Добавляем корень проекта в путь поиска модулей
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from aiogram.types import Update

from src.bot import bot, db, dp, setup_bot_commands
from src.observability import init_sentry

_initialized = False

# Вычисление детерминированного криптографического токена для верификации запросов от Telegram
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = (
    os.getenv("TELEGRAM_WEBHOOK_SECRET")
    or hashlib.sha256(f"ar_secret_{BOT_TOKEN}".encode()).hexdigest()[:32]
)


async def ensure_initialized():
    """Инициализация БД и команд бота при первом холодном старте."""
    global _initialized
    if not _initialized:
        try:
            init_sentry("telegram-bot")
            await db.init_schema()
            await setup_bot_commands()
            _initialized = True
        except Exception as e:
            print(f"⚠️ Ошибка инициализации сервиса: {e}", file=sys.stderr)


async def app(scope, receive, send):
    """Нативный ASGI обработчик для Vercel Serverless Functions с проверкой подписи Telegram."""
    if scope["type"] != "http":
        return

    method = scope.get("method", "GET")
    path = scope.get("path", "/")
    # Vercel rewrite подменяет path на /api/index.py, поэтому команду установки
    # вебхука определяем по query-параметру (path оставлен для обратной совместимости)
    query_string = scope.get("query_string", b"").decode("utf-8", errors="ignore")

    # === GET запросы: Health-check и привязка вебхука ===
    if method == "GET":
        if "/set_webhook" in path or "set_webhook" in query_string:
            # Опциональная защита эндпоинта: если задан WEBHOOK_SETUP_KEY,
            # установка разрешена только по ссылке ?set_webhook=<ключ>
            setup_key = os.getenv("WEBHOOK_SETUP_KEY")
            if setup_key and f"set_webhook={setup_key}" not in query_string:
                forbidden_body = b"Forbidden: invalid or missing setup key"
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(forbidden_body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": forbidden_body})
                return
            try:
                await ensure_initialized()
                headers = dict(scope.get("headers", []))
                host = headers.get(b"host", b"").decode("utf-8")
                proto = headers.get(b"x-forwarded-proto", b"https").decode("utf-8")
                webhook_url = f"{proto}://{host}/"

                await bot.set_webhook(
                    url=webhook_url,
                    secret_token=WEBHOOK_SECRET,
                    drop_pending_updates=True,
                    allowed_updates=["message", "callback_query"],
                )
                info = await bot.get_webhook_info()

                body = json.dumps(
                    {
                        "status": "success",
                        "message": f"Webhook успешно защищен и установлен на {webhook_url}",
                        "webhook_info": {
                            "url": info.url,
                            "pending_update_count": info.pending_update_count,
                            "has_custom_certificate": info.has_custom_certificate,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ).encode()

                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                err_body = f"Ошибка установки Webhook: {e}".encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [
                            (b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(err_body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": err_body})
            return

        # Главная страница (проверка статуса)
        body = (
            b"OK - Anime Radar Bot is active on Vercel 24/7!\n\n"
            b"Endpoint is cryptographically protected with Telegram Secret Token.\n"
        )
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
        return

    # === POST запросы: входящие сообщения и кнопки от Telegram ===
    if method == "POST":
        headers = dict(scope.get("headers", []))
        received_token = headers.get(b"x-telegram-bot-api-secret-token", b"").decode(
            "utf-8", errors="ignore"
        )

        # Строгая верификация: отсекаем любых спамеров или сканеров
        if not hmac.compare_digest(received_token, WEBHOOK_SECRET):
            print(
                "🛑 Отклонён неавторизованный POST-запрос: неверный Secret Token!",
                file=sys.stderr,
            )
            forbidden_body = b"Forbidden: Invalid or missing Telegram secret token"
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(forbidden_body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": forbidden_body})
            return

        body_parts = []
        more_body = True
        while more_body:
            message = await receive()
            body_parts.append(message.get("body", b""))
            more_body = message.get("more_body", False)

        full_body = b"".join(body_parts)

        try:
            await ensure_initialized()
            update_data = json.loads(full_body.decode("utf-8"))
            update = Update.model_validate(update_data, context={"bot": bot})
            await dp.feed_update(bot=bot, update=update)
        except Exception as e:
            print(f"⚠️ Ошибка обработки входящего обновления: {e}", file=sys.stderr)

        resp_body = b"OK"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"content-length", str(len(resp_body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": resp_body})
