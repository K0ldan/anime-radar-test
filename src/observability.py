import os
import re
import sys
from pathlib import Path

import sentry_sdk
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Регулярные выражения для маскирования чувствительных паттернов
RE_TELEGRAM_BOT_TOKEN = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,50}\b")
RE_POSTGRES_URL = re.compile(r"postgres(?:ql)?://[^\s\"']+", re.IGNORECASE)
RE_SENTRY_DSN = re.compile(r"https://[a-f0-9]+@[a-z0-9.-]+/[0-9]+", re.IGNORECASE)

SENSITIVE_KEY_SUBSTRINGS = (
    "chat_id",
    "token",
    "password",
    "secret",
    "auth",
    "dsn",
    "database_url",
    "neon_url",
)


def scrub_text(text: str) -> str:
    """Маскирует секреты (токены, пароли, DSN) в строках."""
    if not isinstance(text, str):
        return text
    text = RE_TELEGRAM_BOT_TOKEN.sub("[REDACTED_TELEGRAM_TOKEN]", text)
    text = RE_POSTGRES_URL.sub("[REDACTED_POSTGRES_URL]", text)
    text = RE_SENTRY_DSN.sub("[REDACTED_SENTRY_DSN]", text)
    return text


def sanitize_event(event, hint):
    """
    Hook before_send: гарантирует, что никакие персональные данные (PII),
    chat_id или секреты не попадут в события Sentry.
    """
    # 1. Принудительно очищаем информацию о пользователе (IP, id, username)
    event["user"] = {}

    # 2. Фильтруем теги и вырезаем потенциальные ключи с chat_id или токенами
    if "tags" in event and isinstance(event["tags"], dict):
        event["tags"] = {
            k: scrub_text(str(v))
            for k, v in event["tags"].items()
            if not any(s in k.lower() for s in SENSITIVE_KEY_SUBSTRINGS)
        }

    # 3. Очищаем хлебные крошки (breadcrumbs) от секретов и URL
    if "breadcrumbs" in event and isinstance(event["breadcrumbs"], dict):
        crumbs = event["breadcrumbs"].get("values", [])
        for crumb in crumbs:
            if "message" in crumb and isinstance(crumb["message"], str):
                crumb["message"] = scrub_text(crumb["message"])
            if "data" in crumb and isinstance(crumb["data"], dict):
                for dk, dv in list(crumb["data"].items()):
                    if any(s in str(dk).lower() for s in SENSITIVE_KEY_SUBSTRINGS):
                        crumb["data"][dk] = "[REDACTED]"
                    elif isinstance(dv, str):
                        crumb["data"][dk] = scrub_text(dv)

    # 4. Очищаем текст сообщения и стек-трейса исключений
    if "logentry" in event and "message" in event["logentry"]:
        event["logentry"]["message"] = scrub_text(event["logentry"]["message"])

    if "exception" in event and "values" in event["exception"]:
        for exc in event["exception"]["values"]:
            if "value" in exc and isinstance(exc["value"], str):
                exc["value"] = scrub_text(exc["value"])

    return event


def filter_tags(extra_tags: dict) -> dict:
    """Удаляет любые теги, содержащие чувствительные данные (chat_id, token и т.д.)."""
    filtered = {}
    for k, v in extra_tags.items():
        key_lower = str(k).lower()
        if any(s in key_lower for s in SENSITIVE_KEY_SUBSTRINGS):
            continue
        filtered[k] = scrub_text(str(v))
    return filtered


def init_sentry(service_name: str = "anime-radar"):
    """
    Инициализирует мониторинг Sentry (100% бесплатно, 5000 событий/мес).
    Включен режим строгой приватности: send_default_pii=False и before_send sanitization.
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        print("⚠️ SENTRY_DSN не указан, Sentry мониторинг отключен.")
        return False

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=1.0,
        environment=os.getenv("ENVIRONMENT", "production"),
        release="anime-radar@1.0.0",
        send_default_pii=False,
        before_send=sanitize_event,
    )
    sentry_sdk.set_tag("service", service_name)
    print(
        f"🛡️ Sentry Observability успешно подключен для [{service_name}] (PII Protection Enabled)!"
    )
    return True


def capture_error(exception: Exception, **extra_tags):
    """Отправляет пойманную ошибку в Sentry с безопасными тегами."""
    safe_tags = filter_tags(extra_tags)
    with sentry_sdk.push_scope() as scope:
        for k, v in safe_tags.items():
            scope.set_tag(k, v)
        sentry_sdk.capture_exception(exception)


def capture_log(message: str, level: str = "info", **extra_tags):
    """Отправляет информационное сообщение или предупреждение в Sentry."""
    safe_tags = filter_tags(extra_tags)
    safe_msg = scrub_text(message)
    with sentry_sdk.push_scope() as scope:
        for k, v in safe_tags.items():
            scope.set_tag(k, v)
        sentry_sdk.capture_message(safe_msg, level=level)
