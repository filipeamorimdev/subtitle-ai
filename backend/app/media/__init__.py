"""Media identity types and provider protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


MediaType = Literal["movie", "series", "episode"]
Capability = Literal["subtitles", "audio", "metadata"]


@dataclass
class MediaRef:
    provider_id: str
    external_id: str
    media_type: MediaType
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    path: str | None = None
    parent_external_id: str | None = None
    bazarr_movie_id: int | None = None
    bazarr_series_id: int | None = None
    bazarr_episode_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LanguageAvailability:
    language_code: str
    language_name: str | None = None
    available: bool = False
    task_status: str | None = None
    task_id: int | None = None


@dataclass
class LocalizationState:
    capability: Capability
    languages: list[LanguageAvailability] = field(default_factory=list)


class MediaProvider(Protocol):
    provider_id: str

    async def search_media(self, query: str) -> list[MediaRef]:
        ...

    async def get_media(self, external_id: str) -> MediaRef | None:
        ...

    async def get_localization_state(self, media: MediaRef) -> LocalizationState:
        ...
