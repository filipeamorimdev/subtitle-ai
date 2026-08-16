"""Provider account / credential service with legacy OpenRouter fallback."""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers.openrouter import PROVIDER_DISPLAY_NAME, PROVIDER_ID
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.secrets import decrypt_secret, encrypt_secret, load_or_create_fernet, mask_secret
from app.db.models import AiProviderAccountRow, SettingsRow

logger = get_logger("ai.credentials")


@dataclass
class ProviderAccountPublic:
    provider_id: str
    display_name: str
    enabled: bool
    configured: bool
    api_key_masked: str | None
    base_url: str | None


class ProviderAccountService:
    def __init__(self, db: Session, fernet: Fernet | None = None) -> None:
        self.db = db
        self.fernet = fernet or load_or_create_fernet(get_app_config().secret_key_path)

    def get_account(self, provider_id: str) -> AiProviderAccountRow | None:
        return self.db.scalar(
            select(AiProviderAccountRow).where(AiProviderAccountRow.provider_id == provider_id)
        )

    def ensure_account(
        self,
        provider_id: str,
        *,
        display_name: str | None = None,
    ) -> AiProviderAccountRow:
        row = self.get_account(provider_id)
        if row is not None:
            return row
        row = AiProviderAccountRow(
            provider_id=provider_id,
            display_name=display_name
            or (PROVIDER_DISPLAY_NAME if provider_id == PROVIDER_ID else provider_id.title()),
            enabled=True,
            api_key_encrypted=None,
            base_url=None,
        )
        # Prefer copying legacy OpenRouter ciphertext without decrypting.
        if provider_id == PROVIDER_ID:
            settings = self.db.get(SettingsRow, 1)
            if settings and settings.openrouter_api_key_encrypted:
                row.api_key_encrypted = settings.openrouter_api_key_encrypted
        self.db.add(row)
        self.db.flush()
        return row

    def get_api_key(self, provider_id: str) -> str | None:
        """Decrypt API key. Prefer generic account; fall back to legacy settings."""
        account = self.get_account(provider_id)
        if account and account.api_key_encrypted:
            return decrypt_secret(self.fernet, account.api_key_encrypted)
        if provider_id == PROVIDER_ID:
            settings = self.db.get(SettingsRow, 1)
            if settings and settings.openrouter_api_key_encrypted:
                return decrypt_secret(self.fernet, settings.openrouter_api_key_encrypted)
        return None

    def is_configured(self, provider_id: str) -> bool:
        return bool(self.get_api_key(provider_id))

    def get_public(self, provider_id: str) -> ProviderAccountPublic:
        account = self.ensure_account(provider_id)
        key = self.get_api_key(provider_id)
        return ProviderAccountPublic(
            provider_id=account.provider_id,
            display_name=account.display_name,
            enabled=bool(account.enabled),
            configured=bool(key),
            api_key_masked=mask_secret(key),
            base_url=account.base_url,
        )

    def list_public(self) -> list[ProviderAccountPublic]:
        # Alpha1: only OpenRouter is a real provider.
        return [self.get_public(PROVIDER_ID)]

    def set_credentials(
        self,
        provider_id: str,
        *,
        api_key: str | None = None,
        clear_api_key: bool = False,
        base_url: str | None = None,
        clear_base_url: bool = False,
        enabled: bool | None = None,
        display_name: str | None = None,
    ) -> ProviderAccountPublic:
        account = self.ensure_account(provider_id, display_name=display_name)
        if display_name is not None:
            account.display_name = display_name.strip() or account.display_name
        if enabled is not None:
            account.enabled = enabled
        if clear_api_key:
            account.api_key_encrypted = None
        elif api_key is not None and api_key.strip():
            ciphertext = encrypt_secret(self.fernet, api_key.strip())
            account.api_key_encrypted = ciphertext
            # Keep legacy settings column in sync for alpha1 rollback safety.
            if provider_id == PROVIDER_ID:
                settings = self.db.get(SettingsRow, 1)
                if settings is not None:
                    settings.openrouter_api_key_encrypted = ciphertext
                    self.db.add(settings)
        if clear_base_url:
            account.base_url = None
        elif base_url is not None:
            account.base_url = base_url.strip() or None

        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)

        # Invalidate provider health cache after credential rotation.
        try:
            from app.ai.providers.registry import get_provider_registry

            provider = get_provider_registry().get_optional(provider_id)
            if provider is not None:
                provider.invalidate_health_cache()
        except Exception:  # noqa: BLE001
            pass

        logger.info("provider=%s credentials_updated configured=%s", provider_id, bool(self.get_api_key(provider_id)))
        return self.get_public(provider_id)

    def clear_credentials(self, provider_id: str) -> ProviderAccountPublic:
        return self.set_credentials(provider_id, clear_api_key=True, clear_base_url=False)
