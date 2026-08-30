"""Jellyfin integration."""

from app.integrations.jellyfin.client import JellyfinClient, JellyfinError

__all__ = ["JellyfinClient", "JellyfinError"]
