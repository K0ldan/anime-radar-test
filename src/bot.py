import asyncio
import html
import os
import sys
import time

from pathlib import Path
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Добавляем корневую папку проекта в sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

load_dotenv(dotenv_path=BASE_DIR / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from src.clients.anilist import AniListClient
from src.database import Database
from src.formatters.telegram import format_telegram_card
from src.observability import capture_error, init_sentry

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db = Database()
anilist = AniListClient()

# --- Безопасный Rate Limiter (защита от спама и DoS) ---
_user_last_action: dict[int, float] = {}
RATE_LIMIT_COOLDOWN = 1.5  # Базовый интервал между запросами (сек)


def check_rate_limit(user_id: int, cooldown: float = RATE_LIMIT_COOLDOWN) -> bool:
    """
    Проверяет частоту запросов пользователя (Rate Limiting).
    Возвращает True, если запрос разрешен, или False, если превышен лимит.
    Автоматически очищает старые записи в памяти.
    """
    now = time.time()
    last = _user_last_action.get(user_id, 0.0)
    if now - last < cooldown:
        return False
    _user_last_action[user_id] = now

    # Периодическая самоочистка для предотвращения роста памяти
    if len(_user_last_action) > 1000:
        cutoff = now - 60.0
        keys_to_del = [uid for uid, t in _user_last_action.items() if t < cutoff]
        for uid in keys_to_del:
            _user_last_action.pop(uid, None)
    return True


MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 Мои подписки"), KeyboardButton(text="🔍 Найти аниме")],
        [
            KeyboardButton(text="⚡ Проверить релизы"),
            KeyboardButton(text="🎲 Что посмотреть?"),
        ],
        [KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
    persistent=True,
)


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Приветственное сообщение, очистка старых кнопок и регистрация."""
    chat_id = message.chat.id
    username = message.from_user.username
    await db.register_user(chat_id, username)

    text = (
        "👋 <b>Добро пожаловать в Anime Radar!</b>\n\n"
        "Я отслеживаю выход новых серий ваших любимых тайтлов и присылаю карточки с рейтингом и таймером выхода.\n\n"
        "Вы можете пользоваться удобными <b>кнопками внизу экрана</b> или просто отправить мне <b>название аниме</b> для поиска!"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: types.Message):
    text = (
        "📖 <b>Справка по боту:</b>\n\n"
        "🔍 <b>Поиск аниме:</b> напишите <code>/track Название</code> или просто отправьте название тайтла в чат (например: <i>Solo Leveling</i>).\n"
        "📋 <b>Мои подписки:</b> покажет все ваши сохраненные аниме со статусами (Онгоинг / Завершено) и кнопками удаления.\n"
        "⚡ <b>Проверить релизы:</b> запустить проверку актуального расписания прямо сейчас.\n"
        "🎲 <b>Что посмотреть?:</b> персональные рекомендации горячих онгоингов на основе жанров вашей коллекции."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)


@dp.message(F.text == "🔍 Найти аниме")
async def btn_search_prompt(message: types.Message):
    await message.answer(
        "🔎 <b>Напишите название аниме прямо в этот чат:</b>\n"
        "<i>Например: Attack on Titan, Naruto, Bleach...</i>",
        parse_mode="HTML",
    )


@dp.message(Command("list"))
@dp.message(F.text == "📋 Мои подписки")
async def cmd_list(message: types.Message):
    """Выводит список всех подписок текущего пользователя."""
    chat_id = message.chat.id
    if not check_rate_limit(chat_id, cooldown=1.0):
        await message.answer("⏳ Подождите секунду...")
        return

    subs = await db.get_user_subscriptions(chat_id)

    if not subs:
        await message.answer(
            "📋 <b>Ваш список отслеживания пуст.</b>\n\n"
            "Нажмите <b>«🔍 Найти аниме»</b> или просто напишите название аниме, чтобы добавить его!",
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    text = f"📋 <b>Ваши отслеживаемые аниме ({len(subs)}):</b>\n\n"
    keyboard_buttons = []

    for idx, s in enumerate(subs, 1):
        safe_title = html.escape(s["title"])
        status = s.get("status")
        status_badge = (
            " 🏁 <i>(Завершено)</i>" if status == "FINISHED" else " 🟢 <i>(Онгоинг)</i>"
        )
        text += (
            f"{idx}. <b><a href='{s['site_url']}'>{safe_title}</a></b>{status_badge}\n"
        )
        keyboard_buttons.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Удалить «{safe_title[:25]}»",
                    callback_data=f"unsub:{s['anime_id']}",
                )
            ]
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer(
        text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True
    )


@dp.message(F.text == "⚡ Проверить релизы")
async def btn_check_releases(message: types.Message):
    """Ручной запуск проверки расписания из Telegram."""
    chat_id = message.chat.id
    if not check_rate_limit(chat_id, cooldown=2.0):
        await message.answer(
            "⏳ Пожалуйста, подождите пару секунд перед повторной проверкой релизов."
        )
        return

    wait_msg = await message.answer("🔄 Проверяю расписание серий на AniList...")

    try:
        # 1. Проверяем персональные подписки пользователя
        subs = await db.get_user_subscriptions(chat_id)
        is_personalized = False

        if subs:
            media_ids = [s["anime_id"] for s in subs]
            schedules = await anilist.get_schedules_for_media_ids(media_ids)
            if schedules:
                is_personalized = True
            else:
                schedules = await anilist.get_upcoming_schedules(
                    limit=3, distinct_anime=True
                )
        else:
            schedules = await anilist.get_upcoming_schedules(
                limit=3, distinct_anime=True
            )

        if not schedules:
            await wait_msg.edit_text(
                "Серий в расписании на ближайшее время не найдено."
            )
            return

        await wait_msg.delete()

        header_text = (
            "🎯 <b>Ближайшие релизы по ВАШИМ подпискам:</b>"
            if is_personalized
            else "⚡ <b>Топ-3 ближайших разных аниме в расписании:</b>"
        )
        await message.answer(header_text, parse_mode="HTML")

        # Отправляем карточки каждого из ближайших аниме
        for schedule in schedules[:3]:
            caption, photo_url, reply_markup = format_telegram_card(schedule)
            try:
                await message.answer_photo(
                    photo=photo_url,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                await asyncio.sleep(0.5)
            except Exception:
                await message.answer(
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
    except Exception as e:
        capture_error(e, context="btn_check_releases")
        await wait_msg.edit_text(f"❌ Ошибка проверки расписания: {e}")


async def perform_search(message: types.Message, query: str):
    """Общий метод поиска аниме с защитой от XSS/HTML инъекций."""
    chat_id = message.chat.id
    if not check_rate_limit(chat_id, cooldown=1.5):
        await message.answer(
            "⏳ Пожалуйста, подождите секунду перед следующим поиском."
        )
        return

    safe_query = html.escape(query)
    wait_msg = await message.answer(
        f"🔎 Ищу <i>«{safe_query}»</i> на AniList...", parse_mode="HTML"
    )

    try:
        results = await anilist.search_anime(query, limit=3)
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка поиска: {e}")
        return

    if not results:
        await wait_msg.edit_text(
            f"😔 По запросу <i>«{safe_query}»</i> ничего не найдено на AniList.",
            parse_mode="HTML",
        )
        return

    await wait_msg.delete()

    for anime in results:
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
        photo_url = anime.coverImage.best_quality

        status_label = html.escape(anime.status_label)
        episodes_info = f" (всего серий: {anime.episodes})" if anime.episodes else ""
        button_text = (
            "📌 В коллекцию (Завершено)"
            if anime.is_finished
            else "➕ Отслеживать новые серии"
        )

        caption = (
            f"🎬 <b>{safe_title}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 Статус: <b>{status_label}</b>{episodes_info}\n"
            f"⭐ Рейтинг: <b>{score}</b>\n"
            f"🏢 Студия: {studio}\n"
            f"🏷 Жанры: {genres}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=button_text,
                        callback_data=f"sub:{anime.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🌐 Страница на AniList",
                        url=anime.siteUrl or f"https://anilist.co/anime/{anime.id}",
                    )
                ],
            ]
        )

        try:
            await message.answer_photo(
                photo=photo_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            await message.answer(text=caption, parse_mode="HTML", reply_markup=keyboard)


@dp.message(Command("recommend"))
@dp.message(F.text == "🎲 Что посмотреть?")
async def btn_recommendations(message: types.Message):
    """Персональный рекомендательный движок на основе жанров коллекции."""
    chat_id = message.chat.id
    if not check_rate_limit(chat_id, cooldown=2.0):
        await message.answer("⏳ Подождите пару секунд перед следующим подбором.")
        return

    wait_msg = await message.answer("🔄 Подбираю лучшие онгоинги по вашим вкусам...")
    fav_genres = await db.get_user_favorite_genres(chat_id)
    subs = await db.get_user_subscriptions(chat_id)
    exclude_ids = [s["anime_id"] for s in subs]

    try:
        recs = await anilist.get_recommendations(
            genres=fav_genres if fav_genres else None,
            limit=2,
            exclude_ids=exclude_ids,
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка подбора рекомендаций: {e}")
        return

    if not recs:
        await wait_msg.edit_text(
            "😔 К сожалению, сейчас не нашлось подходящих онгоингов."
        )
        return

    await wait_msg.delete()

    genre_note = (
        f"<i>(на основе любимых жанров вашей коллекции: {', '.join(fav_genres[:3])})</i>"
        if fav_genres
        else "<i>(топ популярных онгоингов сезона)</i>"
    )

    await message.answer(
        f"💡 <b>Рекомендованные онгоинги для вас:</b>\n{genre_note}",
        parse_mode="HTML",
    )

    for anime in recs:
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
        photo_url = anime.coverImage.best_quality

        caption = (
            f"🎬 <b>{safe_title}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 Статус: <b>🟢 Выходит (Онгоинг)</b>\n"
            f"⭐ Рейтинг: <b>{score}</b>\n"
            f"🏢 Студия: {studio}\n"
            f"🏷 Жанры: {genres}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Отслеживать этот тайтл",
                        callback_data=f"sub:{anime.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🌐 Страница на AniList",
                        url=anime.siteUrl or f"https://anilist.co/anime/{anime.id}",
                    )
                ],
            ]
        )

        try:
            await message.answer_photo(
                photo=photo_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            await asyncio.sleep(0.5)
        except Exception:
            await message.answer(text=caption, parse_mode="HTML", reply_markup=keyboard)


@dp.message(Command("track"))
async def cmd_track(message: types.Message, command: CommandObject):
    query = command.args
    if not query:
        await message.answer(
            "⚠️ Укажите название: <code>/track Chainsaw Man</code>", parse_mode="HTML"
        )
        return
    await perform_search(message, query)


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_plain_text(message: types.Message):
    """Умный поиск при отправке обычного текста в чат."""
    query = message.text.strip()
    await perform_search(message, query)


@dp.callback_query(F.data.startswith("sub:"))
async def callback_subscribe(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    if not check_rate_limit(chat_id, cooldown=1.0):
        await callback.answer(
            "⏳ Слишком частые запросы, подождите немного.", show_alert=False
        )
        return

    anime_id = int(callback.data.split(":")[1])

    raw_caption = callback.message.caption or callback.message.text or ""
    caption_lines = raw_caption.split("\n") if raw_caption else ["Аниме"]
    
    title = "Аниме"
    for line in caption_lines:
        if "🎬" in line:
            clean = re.sub(r"<[^>]+>", "", line).replace("🎬", "").strip()
            clean = re.sub(r"\(Серия \d+\)", "", clean).strip()
            if clean:
                title = clean
                break
    if title == "Аниме":
        title = caption_lines[0].replace("🎬", "").strip()

    is_finished = "Завершено" in raw_caption
    status_val = "FINISHED" if is_finished else "RELEASING"

    genres = []
    for line in caption_lines:
        if "Жанры:" in line:
            genres_raw = re.sub(r"<[^>]+>", "", line).split("Жанры:")[1].strip()
            genres = [g.strip() for g in genres_raw.split(",") if g.strip()]

    await db.add_subscription(
        telegram_chat_id=chat_id,
        anime_id=anime_id,
        title=title,
        site_url=f"https://anilist.co/anime/{anime_id}",
        cover_image_url="",
        status=status_val,
        genres=genres if genres else None,
    )

    if is_finished:
        reply_msg = (
            f"🎉 <b>«{html.escape(title)}»</b> добавлен в вашу коллекцию!\n\n"
            f"ℹ️ <i>Обратите внимание: показ этого сериала уже завершён, новые серии не выходят.</i>"
        )
    else:
        reply_msg = (
            f"🎉 <b>«{html.escape(title)}»</b> добавлен в ваш список отслеживания!\n"
            f"Теперь вы будете получать уведомления о выходе новых серий."
        )

    await callback.answer("✅ Добавлено!", show_alert=True)
    await callback.message.reply(
        reply_msg,
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


@dp.callback_query(F.data.startswith("unsub:"))
async def callback_unsubscribe(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    if not check_rate_limit(chat_id, cooldown=1.0):
        await callback.answer(
            "⏳ Слишком частые запросы, подождите немного.", show_alert=False
        )
        return

    anime_id = int(callback.data.split(":")[1])

    await db.remove_subscription(chat_id, anime_id)
    await callback.answer("Удалено из отслеживания", show_alert=True)
    await cmd_list(callback.message)


async def start_bot():
    print("🤖 Запуск интерактивного бота Anime Radar (Hardened v2)...")
    init_sentry("telegram-bot")
    await db.init_schema()

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Перезапустить бота и меню"),
            BotCommand(command="list", description="Мои отслеживаемые аниме"),
            BotCommand(command="track", description="Найти аниме по названию"),
            BotCommand(
                command="recommend", description="Что посмотреть? (Рекомендации)"
            ),
            BotCommand(command="help", description="Справка по командам"),
        ]
    )

    print("✅ Кнопки и команды обновлены. Бот слушает сообщения!")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(start_bot())
