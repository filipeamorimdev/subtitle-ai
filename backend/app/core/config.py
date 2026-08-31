"""Application runtime configuration."""

from __future__ import annotations

import json
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.media_roots import discover_media_roots


def _parse_media_roots_override(value: object) -> list[str] | None:
    """Parse optional env override; empty/unset means auto-discover."""
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        roots = [str(item).strip() for item in value if str(item).strip()]
        return roots or None
    if isinstance(value, Path):
        return [str(value)]
    text = str(value).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            roots = [str(item).strip() for item in parsed if str(item).strip()]
            return roots or None
    roots = [part.strip() for part in text.split(",") if part.strip()]
    return roots or None


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUBTITLE_AI_", extra="ignore")

    version: str = "0.3.0a2"
    host: str = "0.0.0.0"
    port: int = 6768
    config_dir: Path = Path("/config")
    # Optional override. When unset, roots are discovered from container mounts.
    media_roots: Annotated[list[str] | None, NoDecode] = None
    database_filename: str = "subtitle-ai.db"
    secret_key_filename: str = "secret.key"
    frontend_dist: Path | None = None
    log_level: str = "INFO"
    auth_username: str = "admin"
    auth_password: str | None = None
    auth_forward_header: str | None = None
    # CIDRs/IPs allowed to assert ``auth_forward_header``.  Forward-auth is
    # disabled until this is configured, preventing clients from forging the
    # identity header when the app is directly reachable.
    auth_forward_trusted_proxies: Annotated[list[str] | None, NoDecode] = None
    # 0/unset = auto (half the cores, leaving headroom for the API).
    whisper_cpu_threads: int = 0
    # Recycle the CPU TTS model periodically to bound long-lived inference state.
    # Set to 0 to disable recycling.
    chatterbox_recycle_cues: int = 50
    # Save the cue and stop for a clean retry when CPU inference degrades badly.
    # Set to 0 to disable the slow-cue guard.
    chatterbox_max_cue_seconds: float = 600.0
    # A healthy CPU-only episode can take longer than the previous six-hour cap.
    dub_max_runtime_hours: float = 12.0
    # Per-task debug traces under {config_dir}/debug. Independent of log_level.
    debug_trace: bool = False

    @field_validator("media_roots", mode="before")
    @classmethod
    def parse_media_roots(cls, value: object) -> list[str] | None:
        return _parse_media_roots_override(value)

    @field_validator("auth_forward_trusted_proxies", mode="before")
    @classmethod
    def parse_auth_forward_trusted_proxies(cls, value: object) -> list[str] | None:
        return _parse_media_roots_override(value)

    @cached_property
    def resolved_media_roots(self) -> list[str]:
        if self.media_roots:
            return list(self.media_roots)
        return discover_media_roots(config_dir=self.config_dir)

    @property
    def database_url(self) -> str:
        db_path = self.config_dir / self.database_filename
        return f"sqlite:///{db_path}"

    @property
    def secret_key_path(self) -> Path:
        return self.config_dir / self.secret_key_filename

    def ensure_directories(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "logs").mkdir(parents=True, exist_ok=True)
        (self.config_dir / "logs" / "jobs").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_app_config() -> AppConfig:
    return AppConfig()
