import time
from typing import Any

import httpx

from src.models.anime import AiringSchedule, Media

ANILIST_API_URL = "https://graphql.anilist.co"


class AniListAPIError(Exception):
    """Базовое исключение для ошибок AniList API."""


class AniListUnavailableError(AniListAPIError):
    """Вызывается, когда сервис AniList недоступен (техработы, 403, 502, 503, таймаут)."""


GRAPHQL_SEARCH_QUERY = """
query SearchAnime($search: String, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
      id
      title {
        romaji
        english
      }
      averageScore
      popularity
      coverImage {
        extraLarge
        large
      }
      genres
      studios(isMain: true) {
        nodes {
          name
        }
      }
      siteUrl
      status
      episodes
    }
  }
}
"""

GRAPHQL_RECOMMENDATIONS_QUERY = """
query GetRecommendations($genre: String, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(genre: $genre, status: RELEASING, sort: [SCORE_DESC, POPULARITY_DESC], type: ANIME) {
      id
      title {
        romaji
        english
      }
      averageScore
      popularity
      coverImage {
        extraLarge
        large
      }
      genres
      studios(isMain: true) {
        nodes {
          name
        }
      }
      siteUrl
      status
      episodes
    }
  }
}
"""

GRAPHQL_TRACKED_QUERY = """
query GetTrackedSchedules($mediaIds: [Int]) {
  Page(page: 1, perPage: 50) {
    airingSchedules(mediaId_in: $mediaIds, notYetAired: true, sort: TIME) {
      id
      episode
      airingAt
      media {
        id
        title {
          romaji
          english
        }
        averageScore
        popularity
        coverImage {
          extraLarge
          large
        }
        genres
        studios(isMain: true) {
          nodes {
            name
          }
        }
        siteUrl
        status
        episodes
      }
    }
  }
}
"""

GRAPHQL_RECENT_AIRING_QUERY = """
query GetRecentAiringSchedules($airingAt_greater: Int, $airingAt_lesser: Int, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    airingSchedules(airingAt_greater: $airingAt_greater, airingAt_lesser: $airingAt_lesser, sort: TIME_DESC) {
      id
      episode
      airingAt
      media {
        id
        title {
          romaji
          english
        }
        averageScore
        popularity
        coverImage {
          extraLarge
          large
        }
        genres
        studios(isMain: true) {
          nodes {
            name
          }
        }
        siteUrl
        status
        episodes
      }
    }
  }
}
"""

GRAPHQL_QUERY = """
query GetUpcomingSchedules($perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    airingSchedules(notYetAired: true, sort: TIME) {
      id
      episode
      airingAt
      media {
        id
        title {
          romaji
          english
        }
        averageScore
        popularity
        coverImage {
          extraLarge
          large
        }
        genres
        studios(isMain: true) {
          nodes {
            name
          }
        }
        siteUrl
        status
        episodes
      }
    }
  }
}
"""


class AniListClient:
    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout

    async def _post(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Выполняет GraphQL запрос к AniList с обработкой технических регламентов,
        таймаутов и защитой от падений при сбоях на стороне AniList.
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AnimeRadar/1.0 (+https://github.com/K0ldan/anime-radar-test)",
        }
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    ANILIST_API_URL, json=payload, headers=headers
                )
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise AniListUnavailableError(
                f"Сетевая ошибка или таймаут при обращении к AniList: {e}"
            ) from e

        if response.is_error:
            err_msg = ""
            try:
                err_data = response.json()
                if isinstance(err_data, dict) and err_data.get("errors"):
                    err_msg = str(err_data["errors"][0].get("message", ""))
            except Exception:  # noqa: BLE001
                err_msg = ""

            status = response.status_code
            if (
                status in (403, 429, 500, 502, 503, 504)
                or "temporarily disabled" in err_msg.lower()
            ):
                detailed_msg = err_msg or f"HTTP {status}"
                raise AniListUnavailableError(
                    f"Сервис AniList временно недоступен ({detailed_msg})"
                )

            response.raise_for_status()

        data: dict[str, Any] = response.json()
        if data.get("errors"):
            first_err = data["errors"][0].get("message", "Неизвестная ошибка GraphQL")
            if "temporarily disabled" in first_err.lower():
                raise AniListUnavailableError(
                    f"Сервис AniList временно недоступен ({first_err})"
                )
            raise AniListAPIError(f"AniList GraphQL Error: {first_err}")

        return data

    async def get_upcoming_schedules(
        self, limit: int = 5, distinct_anime: bool = True
    ) -> list[AiringSchedule]:
        """
        Запрашивает расписание релизов серий с AniList GraphQL.
        100% бесплатно, без ключей и без карт.
        """
        # Запрашиваем с запасом (30), чтобы легко отфильтровать уникальные тайтлы
        fetch_count = 30 if distinct_anime else limit

        data = await self._post(GRAPHQL_QUERY, variables={"perPage": fetch_count})

        raw_schedules = data.get("data", {}).get("Page", {}).get("airingSchedules", [])

        parsed_schedules: list[AiringSchedule] = []
        seen_media_ids: set[int] = set()

        for raw_item in raw_schedules:
            try:
                schedule = AiringSchedule.model_validate(raw_item)

                if distinct_anime:
                    if schedule.media.id in seen_media_ids:
                        continue
                    seen_media_ids.add(schedule.media.id)

                parsed_schedules.append(schedule)

                if len(parsed_schedules) == limit:
                    break
            except Exception as e:
                # В случае неожиданного формата логируем и пропускаем одну битую запись
                print(f"⚠️ Ошибка валидации Pydantic для записи: {e}")
                continue

        return parsed_schedules

    async def get_schedules_for_media_ids(
        self, media_ids: list[int]
    ) -> list[AiringSchedule]:
        """Запрашивает расписание только для тех тайтлов, на которые подписаны пользователи."""
        if not media_ids:
            return []

        data = await self._post(
            GRAPHQL_TRACKED_QUERY, variables={"mediaIds": media_ids}
        )

        raw_schedules = data.get("data", {}).get("Page", {}).get("airingSchedules", [])
        results: list[AiringSchedule] = []
        for raw_item in raw_schedules:
            try:
                results.append(AiringSchedule.model_validate(raw_item))
            except Exception as e:
                print(f"⚠️ Ошибка валидации Pydantic: {e}")
                continue
        return results

    async def get_recent_airing_schedules(
        self, hours_back: int = 12, limit: int = 50
    ) -> list[AiringSchedule]:
        """
        Запрашивает серии, которые вышли в эфир за последние `hours_back` часов.
        Используется для механики «Поймано в эфире» (Live Airing Match).
        """
        now = int(time.time())
        past = now - hours_back * 3600

        data = await self._post(
            GRAPHQL_RECENT_AIRING_QUERY,
            variables={
                "airingAt_greater": past,
                "airingAt_lesser": now,
                "perPage": limit,
            },
        )

        raw_schedules = data.get("data", {}).get("Page", {}).get("airingSchedules", [])
        results: list[AiringSchedule] = []
        for raw_item in raw_schedules:
            try:
                results.append(AiringSchedule.model_validate(raw_item))
            except Exception as e:
                print(f"⚠️ Ошибка валидации AiringSchedule: {e}")
                continue
        return results

    async def search_anime(self, query_str: str, limit: int = 3) -> list[Media]:
        """Поиск аниме на AniList по названию (100% бесплатно)."""
        data = await self._post(
            GRAPHQL_SEARCH_QUERY,
            variables={"search": query_str, "perPage": limit},
        )

        raw_media_list = data.get("data", {}).get("Page", {}).get("media", [])
        results: list[Media] = []
        for raw_item in raw_media_list:
            try:
                results.append(Media.model_validate(raw_item))
            except Exception as e:
                print(f"⚠️ Ошибка валидации Media: {e}")
                continue
        return results

    async def get_recommendations(
        self,
        genres: list[str] | None = None,
        limit: int = 2,
        exclude_ids: list[int] | None = None,
    ) -> list[Media]:
        """
        Рекомендательный движок: находит лучшие онгоинги по списку любимых жанров.
        Исключает тайтлы, которые пользователь уже отслеживает.
        """
        exclude_set = set(exclude_ids or [])
        target_genre = genres[0] if genres and len(genres) > 0 else None
        variables: dict[str, Any] = {"perPage": limit + len(exclude_set) + 5}
        if target_genre:
            variables["genre"] = target_genre

        data = await self._post(GRAPHQL_RECOMMENDATIONS_QUERY, variables=variables)

        raw_media_list = data.get("data", {}).get("Page", {}).get("media", [])
        results: list[Media] = []
        for raw_item in raw_media_list:
            if raw_item.get("id") in exclude_set:
                continue
            try:
                results.append(Media.model_validate(raw_item))
                if len(results) >= limit:
                    break
            except Exception as e:
                print(f"⚠️ Ошибка валидации Media рекомендации: {e}")
                continue

        # Если по жанру вернулось мало, добираем из общего топа актуальных онгоингов
        if len(results) < limit and target_genre is not None:
            fallback = await self.get_recommendations(
                genres=None,
                limit=limit - len(results),
                exclude_ids=list(exclude_set) + [r.id for r in results],
            )
            results.extend(fallback)

        return results
