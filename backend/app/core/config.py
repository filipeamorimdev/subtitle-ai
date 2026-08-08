"""Application runtime configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUBTITLE_AI_", extra="ignore")

    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 6768
    config_dir: Path = Path("/config")
    media_root_default: Path = Path("/media")
    database_filename: str = "subtitle-ai.db"
    secret_key_filename: str = "secret.key"
    frontend_dist: Path | None = None
    log_level: str = "INFO"

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
