"""Locked per-series (or movie) glossary terms for consistent names."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GlossaryEntryRow, MediaItemRow

_NORMALIZE_RE = re.compile(r"\s+")


def normalize_source(value: str) -> str:
    return _NORMALIZE_RE.sub(" ", (value or "").strip().lower())


def scope_key_for_media(media: MediaItemRow) -> str:
    if media.bazarr_series_id is not None:
        return f"series:{media.bazarr_series_id}"
    if media.bazarr_movie_id is not None:
        return f"movie:{media.bazarr_movie_id}"
    return f"media:{media.id}"


class GlossaryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_entries(
        self,
        *,
        scope_key: str,
        target_language: str,
    ) -> list[GlossaryEntryRow]:
        return list(
            self.db.scalars(
                select(GlossaryEntryRow)
                .where(
                    GlossaryEntryRow.scope_key == scope_key,
                    GlossaryEntryRow.target_language == target_language,
                )
                .order_by(GlossaryEntryRow.source.asc())
            ).all()
        )

    def replace_entries(
        self,
        *,
        scope_key: str,
        target_language: str,
        entries: list[dict[str, Any]],
    ) -> list[GlossaryEntryRow]:
        existing = self.list_entries(scope_key=scope_key, target_language=target_language)
        for row in existing:
            self.db.delete(row)
        saved: list[GlossaryEntryRow] = []
        seen: set[str] = set()
        for item in entries:
            source = str(item.get("source") or "").strip()
            target = str(item.get("target") or "").strip()
            if not source or not target:
                continue
            key = normalize_source(source)
            if key in seen:
                continue
            seen.add(key)
            row = GlossaryEntryRow(
                scope_key=scope_key,
                target_language=target_language,
                source=source,
                source_normalized=key,
                target=target,
                locked=bool(item.get("locked", True)),
            )
            self.db.add(row)
            saved.append(row)
        self.db.commit()
        for row in saved:
            self.db.refresh(row)
        return saved

    def prompt_block(self, *, scope_key: str, target_language: str) -> str:
        rows = [r for r in self.list_entries(scope_key=scope_key, target_language=target_language) if r.locked]
        if not rows:
            return ""
        lines = ["Locked glossary (keep these translations exactly):"]
        for row in rows:
            lines.append(f"- {row.source} → {row.target}")
        return "\n".join(lines) + "\n"
