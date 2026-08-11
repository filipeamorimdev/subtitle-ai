"""Settings service."""

from __future__ import annotations

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.api.schemas import LanguageOut, PathMappingIn, SettingsOut, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import decrypt_secret, encrypt_secret, load_or_create_fernet, mask_secret
from app.db.models import SettingsRow


class SettingsService:
    def __init__(self, db: Session, fernet: Fernet | None = None) -> None:
        self.db = db
        self.fernet = fernet or load_or_create_fernet(get_app_config().secret_key_path)

    def get_or_create_row(self) -> SettingsRow:
        row = self.db.get(SettingsRow, 1)
        if row is None:
            config = get_app_config()
            row = SettingsRow(
                id=1,
                openrouter_model="openai/gpt-4o-mini",
                target_language_code="pt-PT",
                target_language_name="Portuguese (Portugal)",
                source_languages=["en"],
                media_roots=list(config.resolved_media_roots),
                path_mappings=[],
                batch_size=25,
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        return row

    def get_public(self) -> SettingsOut:
        row = self.get_or_create_row()
        config = get_app_config()
        bazarr_key = decrypt_secret(self.fernet, row.bazarr_api_key_encrypted)
        openrouter_key = decrypt_secret(self.fernet, row.openrouter_api_key_encrypted)
        return SettingsOut(
            bazarr_url=row.bazarr_url,
            bazarr_api_key_masked=mask_secret(bazarr_key),
            bazarr_api_key_configured=bool(bazarr_key),
            openrouter_api_key_masked=mask_secret(openrouter_key),
            openrouter_api_key_configured=bool(openrouter_key),
            openrouter_model=row.openrouter_model,
            target_language=LanguageOut(
                code=row.target_language_code,
                name=row.target_language_name,
            ),
            source_languages=list(row.source_languages or ["en"]),
            media_roots=list(config.resolved_media_roots),
            path_mappings=[PathMappingIn(**m) for m in (row.path_mappings or [])],
            batch_size=row.batch_size,
        )

    def update(self, payload: SettingsUpdate) -> SettingsOut:
        row = self.get_or_create_row()
        if payload.bazarr_url is not None:
            row.bazarr_url = payload.bazarr_url.strip() or None
        if payload.clear_bazarr_api_key:
            row.bazarr_api_key_encrypted = None
        elif payload.bazarr_api_key:
            row.bazarr_api_key_encrypted = encrypt_secret(self.fernet, payload.bazarr_api_key.strip())
        if payload.clear_openrouter_api_key:
            row.openrouter_api_key_encrypted = None
        elif payload.openrouter_api_key:
            row.openrouter_api_key_encrypted = encrypt_secret(
                self.fernet, payload.openrouter_api_key.strip()
            )
        if payload.openrouter_model is not None:
            row.openrouter_model = payload.openrouter_model.strip() or row.openrouter_model
        if payload.target_language_code is not None:
            row.target_language_code = payload.target_language_code.strip()
        if payload.target_language_name is not None:
            row.target_language_name = payload.target_language_name.strip()
        if payload.source_languages is not None:
            row.source_languages = payload.source_languages or ["en"]
        if payload.path_mappings is not None:
            row.path_mappings = [m.model_dump() for m in payload.path_mappings]
        if payload.batch_size is not None:
            row.batch_size = payload.batch_size
        self.db.add(row)
        self.db.commit()
        return self.get_public()

    def get_bazarr_credentials(self) -> tuple[str | None, str | None]:
        row = self.get_or_create_row()
        return row.bazarr_url, decrypt_secret(self.fernet, row.bazarr_api_key_encrypted)

    def get_openrouter_credentials(self) -> tuple[str | None, str]:
        row = self.get_or_create_row()
        key = decrypt_secret(self.fernet, row.openrouter_api_key_encrypted)
        return key, row.openrouter_model
