"""Settings service."""

from __future__ import annotations

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.api.schemas import LanguageOut, PathMappingIn, SettingsOut, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import decrypt_secret, encrypt_secret, load_or_create_fernet, mask_secret
from app.db.models import SettingsRow
from app.services.ai_cost import micro_to_usd, usd_to_micro


def _bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _micro_to_usd(value: object) -> float | None:
    if value is None:
        return None
    try:
        return micro_to_usd(int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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
                max_concurrent_translate=1,
                max_concurrent_extract=1,
                max_concurrent_request=1,
                automatic_fallback_enabled=False,
                automatic_scan_interval_minutes=5,
                bazarr_grace_period_minutes=10,
                automatic_retry_enabled=True,
                maximum_automatic_retries=3,
                openrouter_log_full_exchanges=False,
                routing_strategy="free_first",
                allow_paid_fallback=False,
                allow_free_fallback=True,
                allow_unknown_pricing=False,
                monthly_budget_enabled=False,
                allow_manual_budget_override=False,
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        self._ensure_legacy_preference(row)
        return row

    def _ensure_legacy_preference(self, row: SettingsRow) -> None:
        try:
            from app.services.model_preferences import seed_legacy_model_preference

            seeded = seed_legacy_model_preference(self.db)
            if seeded is not None:
                self.db.commit()
                self.db.refresh(row)
        except Exception:  # noqa: BLE001
            self.db.rollback()

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
            max_concurrent_translate=row.max_concurrent_translate,
            max_concurrent_extract=row.max_concurrent_extract,
            max_concurrent_request=row.max_concurrent_request,
            automatic_fallback_enabled=_bool(
                getattr(row, "automatic_fallback_enabled", False), False
            ),
            automatic_scan_interval_minutes=_int(
                getattr(row, "automatic_scan_interval_minutes", 5),
                5,
                minimum=1,
                maximum=1440,
            ),
            bazarr_grace_period_minutes=_int(
                getattr(row, "bazarr_grace_period_minutes", 10),
                10,
                minimum=0,
                maximum=1440,
            ),
            automatic_retry_enabled=_bool(
                getattr(row, "automatic_retry_enabled", True), True
            ),
            maximum_automatic_retries=_int(
                getattr(row, "maximum_automatic_retries", 3),
                3,
                minimum=0,
                maximum=20,
            ),
            openrouter_log_full_exchanges=_bool(
                getattr(row, "openrouter_log_full_exchanges", False), False
            ),
            routing_strategy=getattr(row, "routing_strategy", None) or "free_first",
            allow_paid_fallback=_bool(getattr(row, "allow_paid_fallback", False), False),
            allow_free_fallback=_bool(getattr(row, "allow_free_fallback", True), True),
            allow_unknown_pricing=_bool(getattr(row, "allow_unknown_pricing", False), False),
            maximum_cost_per_job_usd=_micro_to_usd(
                getattr(row, "maximum_cost_per_job_micro_usd", None)
            ),
            monthly_budget_enabled=_bool(getattr(row, "monthly_budget_enabled", False), False),
            monthly_budget_amount_usd=_micro_to_usd(
                getattr(row, "monthly_budget_amount_micro_usd", None)
            ),
            allow_manual_budget_override=_bool(
                getattr(row, "allow_manual_budget_override", False), False
            ),
        )

    def concurrency_limits(self) -> dict[str, int]:
        row = self.get_or_create_row()
        return {
            "translate": max(1, int(row.max_concurrent_translate or 1)),
            "extract": max(1, int(row.max_concurrent_extract or 1)),
            "request": max(1, int(row.max_concurrent_request or 1)),
        }

    def is_automatic_fallback_enabled(self) -> bool:
        return self.get_public().automatic_fallback_enabled

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
        if payload.max_concurrent_translate is not None:
            row.max_concurrent_translate = payload.max_concurrent_translate
        if payload.max_concurrent_extract is not None:
            row.max_concurrent_extract = payload.max_concurrent_extract
        if payload.max_concurrent_request is not None:
            row.max_concurrent_request = payload.max_concurrent_request
        if payload.automatic_fallback_enabled is not None:
            row.automatic_fallback_enabled = payload.automatic_fallback_enabled
        if payload.automatic_scan_interval_minutes is not None:
            row.automatic_scan_interval_minutes = _int(
                payload.automatic_scan_interval_minutes,
                5,
                minimum=1,
                maximum=1440,
            )
        if payload.bazarr_grace_period_minutes is not None:
            row.bazarr_grace_period_minutes = _int(
                payload.bazarr_grace_period_minutes,
                10,
                minimum=0,
                maximum=1440,
            )
        if payload.automatic_retry_enabled is not None:
            row.automatic_retry_enabled = payload.automatic_retry_enabled
        if payload.maximum_automatic_retries is not None:
            row.maximum_automatic_retries = _int(
                payload.maximum_automatic_retries,
                3,
                minimum=0,
                maximum=20,
            )
        if payload.openrouter_log_full_exchanges is not None:
            row.openrouter_log_full_exchanges = payload.openrouter_log_full_exchanges
        if payload.routing_strategy is not None:
            row.routing_strategy = payload.routing_strategy
        if payload.allow_paid_fallback is not None:
            row.allow_paid_fallback = payload.allow_paid_fallback
        if payload.allow_free_fallback is not None:
            row.allow_free_fallback = payload.allow_free_fallback
        if payload.allow_unknown_pricing is not None:
            row.allow_unknown_pricing = payload.allow_unknown_pricing
        if payload.clear_maximum_cost_per_job:
            row.maximum_cost_per_job_micro_usd = None
        elif payload.maximum_cost_per_job_usd is not None:
            row.maximum_cost_per_job_micro_usd = usd_to_micro(payload.maximum_cost_per_job_usd)
        if payload.monthly_budget_enabled is not None:
            row.monthly_budget_enabled = payload.monthly_budget_enabled
        if payload.clear_monthly_budget_amount:
            row.monthly_budget_amount_micro_usd = None
        elif payload.monthly_budget_amount_usd is not None:
            row.monthly_budget_amount_micro_usd = usd_to_micro(payload.monthly_budget_amount_usd)
        if payload.allow_manual_budget_override is not None:
            row.allow_manual_budget_override = payload.allow_manual_budget_override
        self.db.add(row)
        self.db.commit()
        # Mirror OpenRouter credentials into ai_provider_accounts (same ciphertext).
        if payload.clear_openrouter_api_key or payload.openrouter_api_key:
            try:
                from app.ai.credentials import ProviderAccountService
                from app.ai.providers.registry import get_provider_registry

                accounts = ProviderAccountService(self.db, self.fernet)
                account = accounts.ensure_account("openrouter")
                account.api_key_encrypted = row.openrouter_api_key_encrypted
                self.db.add(account)
                self.db.commit()
                provider = get_provider_registry().get_optional("openrouter")
                if provider is not None:
                    provider.invalidate_health_cache()
            except Exception:  # noqa: BLE001
                self.db.rollback()
        from app.services.model_preferences import seed_legacy_model_preference

        seed_legacy_model_preference(self.db)
        self.db.commit()
        return self.get_public()

    def get_bazarr_credentials(self) -> tuple[str | None, str | None]:
        row = self.get_or_create_row()
        return row.bazarr_url, decrypt_secret(self.fernet, row.bazarr_api_key_encrypted)

    def get_openrouter_credentials(self) -> tuple[str | None, str]:
        row = self.get_or_create_row()
        # Prefer generic provider account; fall back to legacy settings column.
        try:
            from app.ai.credentials import ProviderAccountService

            key = ProviderAccountService(self.db, self.fernet).get_api_key("openrouter")
        except Exception:  # noqa: BLE001
            key = decrypt_secret(self.fernet, row.openrouter_api_key_encrypted)
        if key is None:
            key = decrypt_secret(self.fernet, row.openrouter_api_key_encrypted)
        return key, row.openrouter_model
