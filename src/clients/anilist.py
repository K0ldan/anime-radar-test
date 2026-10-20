import httpx
from src.models.anime import AiringSchedule, Media

ANILIST_API_URL = "https://graphql.anilist.co"

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
      }
    }
  }
}
"""


class AniListClient:
    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout

    async def get_upcoming_schedules(
        self, limit: int = 5, distinct_anime: bool = True
    ) -> list[AiringSchedule]:
        """
        Запрашивает расписание релизов серий с AniList GraphQL.
        100% бесплатно, без ключей и без карт.
        """
        # Запрашиваем с запасом (30), чтобы легко отфильтровать уникальные тайтлы
        fetch_count = 30 if distinct_anime else limit

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                ANILIST_API_URL,
                json={"query": GRAPHQL_QUERY, "variables": {"perPage": fetch_count}},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

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

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                ANILIST_API_URL,
                json={
                    "query": GRAPHQL_TRACKED_QUERY,
                    "variables": {"mediaIds": media_ids},
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        raw_schedules = data.get("data", {}).get("Page", {}).get("airingSchedules", [])
        results: list[AiringSchedule] = []
        for raw_item in raw_schedules:
            try:
                results.append(AiringSchedule.model_validate(raw_item))
            except Exception as e:
                print(f"⚠️ Ошибка валидации Pydantic: {e}")
                continue
        return results

    async def search_anime(self, query_str: str, limit: int = 3) -> list[Media]:
        """Поиск аниме на AniList по названию (100% бесплатно)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                ANILIST_API_URL,
                json={
                    "query": GRAPHQL_SEARCH_QUERY,
                    "variables": {"search": query_str, "perPage": limit},
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        raw_media_list = data.get("data", {}).get("Page", {}).get("media", [])
        results: list[Media] = []
        for raw_item in raw_media_list:
            try:
                results.append(Media.model_validate(raw_item))
            except Exception as e:
                print(f"⚠️ Ошибка валидации Media: {e}")
                continue
        return results
