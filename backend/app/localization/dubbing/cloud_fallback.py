"""Opt-in whole-episode cloud dub fallback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class CloudDubError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloudDubRequest:
    media_path: Path
    target_language: str
    source_language: str = "en"
    rights_acknowledged: bool = False


@dataclass(frozen=True)
class CloudDubResult:
    provider: str
    output_path: Path
    detail: str


class CloudDubProvider(Protocol):
    name: str

    async def dub_episode(self, request: CloudDubRequest) -> CloudDubResult:
        ...


class ElevenLabsCloudDubProvider:
    name = "elevenlabs"

    def __init__(self, *, api_key: str | None, enabled: bool) -> None:
        self.api_key = (api_key or "").strip()
        self.enabled = enabled

    async def dub_episode(self, request: CloudDubRequest) -> CloudDubResult:
        if not self.enabled:
            raise CloudDubError("Cloud dub fallback is disabled in settings.")
        if not request.rights_acknowledged:
            raise CloudDubError("Cloud dub fallback requires explicit rights acknowledgement.")
        if not self.api_key:
            raise CloudDubError("ElevenLabs API key is not configured.")
        raise CloudDubError(
            "Whole-episode ElevenLabs dub fallback is configured but not yet wired in this build. "
            "Approve local character voices or retry after enabling the provider integration."
        )


def build_cloud_provider(*, api_key: str | None, enabled: bool) -> CloudDubProvider:
    return ElevenLabsCloudDubProvider(api_key=api_key, enabled=enabled)
