"""Select Jellyfin when available, with Bazarr as the catalog fallback."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.integrations.bazarr.client import BazarrClient, BazarrError
from app.integrations.jellyfin.client import JellyfinClient, JellyfinError
from app.media import MediaRef
from app.media.bazarr_provider import BAZARR_PROVIDER_ID, BazarrMediaProvider
from app.media.jellyfin_provider import JELLYFIN_PROVIDER_ID, JellyfinMediaProvider
from app.services.settings import SettingsService


class MediaCatalogError(Exception):
    pass


@dataclass
class MediaCatalog:
    jellyfin: JellyfinMediaProvider | None
    bazarr: BazarrMediaProvider | None

    async def search(self, query: str) -> tuple[str, list[MediaRef]]:
        jellyfin_error: Exception | None = None
        if self.jellyfin is not None:
            try:
                return JELLYFIN_PROVIDER_ID, await self.jellyfin.search_media(query)
            except JellyfinError as exc:
                jellyfin_error = exc
        if self.bazarr is not None:
            try:
                return BAZARR_PROVIDER_ID, await self.bazarr.search_media(query)
            except BazarrError as exc:
                raise MediaCatalogError(str(exc)) from exc
        if jellyfin_error is not None:
            raise MediaCatalogError(str(jellyfin_error)) from jellyfin_error
        raise MediaCatalogError("No media catalog is configured.")

    async def get(self, provider_id: str, external_id: str) -> MediaRef | None:
        provider = (provider_id or BAZARR_PROVIDER_ID).lower()
        try:
            if provider == JELLYFIN_PROVIDER_ID and self.jellyfin is not None:
                return await self.jellyfin.get_media(external_id)
            if provider == BAZARR_PROVIDER_ID and self.bazarr is not None:
                return await self.bazarr.get_media(external_id)
        except (JellyfinError, BazarrError):
            return None
        return None


def configured_media_catalog(db: Session) -> MediaCatalog:
    settings = SettingsService(db)
    jellyfin_url, jellyfin_key = settings.get_jellyfin_credentials()
    bazarr_url, bazarr_key = settings.get_bazarr_credentials()
    jellyfin = (
        JellyfinMediaProvider(JellyfinClient(jellyfin_url, jellyfin_key))
        if jellyfin_url and jellyfin_key
        else None
    )
    bazarr = BazarrMediaProvider(BazarrClient(bazarr_url, bazarr_key)) if bazarr_url else None
    return MediaCatalog(jellyfin=jellyfin, bazarr=bazarr)
