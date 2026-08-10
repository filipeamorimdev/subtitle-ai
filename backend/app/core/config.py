"""Application runtime configuration."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_media_roots(value: object) -> list[str]:
    if value is None or value == "":
        return ["/media"]
    if isinstance(value, (list, tuple)):
        roots = [str(item).strip() for item in value if str(item).strip()]
        return roots or ["/media"]
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
            return roots or ["/media"]
    roots = [part.strip() for part in text.split(",") if part.strip()]
    return roots or ["/media"]


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUBTITLE_AI_", extra="ignore")

    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 6768
    config_dir: Path = Path("/config")
    media_roots: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["/media"])
    database_filename: str = "subtitle-ai.db"
    secret_key_filename: str = "secret.key"
    frontend_dist: Path | None = None
    log_level: str = "INFO"

    @field_validator("media_roots", mode="before")
    @classmethod
    def parse_media_roots(cls, value: object) -> list[str]:
        return _parse_media_roots(value)

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
