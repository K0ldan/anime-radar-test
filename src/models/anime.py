from datetime import UTC, datetime, timedelta, timezone

from pydantic import BaseModel, Field

# Часовой пояс МСК (UTC+3)
MSK_TIMEZONE = timezone(timedelta(hours=3))


class MediaTitle(BaseModel):
    romaji: str | None = None
    english: str | None = None
    native: str | None = None


class CoverImage(BaseModel):
    extraLarge: str | None = None
    large: str | None = None
    medium: str | None = None

    @property
    def best_quality(self) -> str:
        return self.extraLarge or self.large or self.medium or ""


class StudioNode(BaseModel):
    name: str


class StudioConnection(BaseModel):
    nodes: list[StudioNode] = Field(default_factory=list)


class Media(BaseModel):
    id: int
    title: MediaTitle
    averageScore: int | None = None
    popularity: int | None = None
    genres: list[str] = Field(default_factory=list)
    studios: StudioConnection = Field(default_factory=StudioConnection)
    coverImage: CoverImage
    siteUrl: str | None = None

    @property
    def display_title(self) -> str:
        return self.title.english or self.title.romaji or "Без названия"

    @property
    def primary_studio(self) -> str:
        if self.studios.nodes:
            return self.studios.nodes[0].name
        return "Не указана"

    @property
    def rating_badge(self) -> str:
        if not self.averageScore:
            return "Оценок пока нет 🆕"
        score_10 = self.averageScore / 10.0
        if score_10 >= 8.5:
            return f"⭐ <b>{score_10:.1f} / 10</b> 🔥 <i>Шедевр сезона</i>"
        elif score_10 >= 7.5:
            return f"⭐ <b>{score_10:.1f} / 10</b> 🟢 <i>Высокий интерес</i>"
        elif score_10 >= 6.5:
            return f"⭐ <b>{score_10:.1f} / 10</b> 🟡 <i>Средний рейтинг</i>"
        else:
            return f"⭐ <b>{score_10:.1f} / 10</b> ⚪ <i>Нишевый тайтл</i>"


class AiringSchedule(BaseModel):
    id: int
    episode: int
    airingAt: int
    media: Media

    @property
    def release_datetime_msk(self) -> datetime:
        """Переводит Unix timestamp в время МСК."""
        return datetime.fromtimestamp(self.airingAt, tz=UTC).astimezone(MSK_TIMEZONE)

    @property
    def countdown_str(self) -> str:
        """Считает оставшееся время до выхода серии."""
        now = datetime.now(UTC)
        target = datetime.fromtimestamp(self.airingAt, tz=UTC)
        diff = target - now

        if diff.total_seconds() <= 0:
            return "⚡ <b>Серия уже вышла в эфир!</b>"

        total_seconds = int(diff.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days} дн.")
        if hours > 0:
            parts.append(f"{hours} ч.")
        parts.append(f"{minutes} мин.")

        return f"⏳ через <b>{' '.join(parts)}</b>"
