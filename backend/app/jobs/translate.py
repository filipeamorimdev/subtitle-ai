"""Translate-job helpers (prompts and reading-speed repair)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import JobRow, LocalizationTaskRow, MediaItemRow
from app.localization.glossary import GlossaryService, scope_key_for_media
from app.localization.locale_notes import note_for


def extra_prompt_context(db: Session, row: JobRow) -> tuple[str, str]:
    """Return (locale_note, glossary_block) for the translation system prompt."""
    locale = note_for(row.target_language, db)
    glossary = ""
    task_id = getattr(row, "task_id", None)
    if task_id:
        task = db.get(LocalizationTaskRow, int(task_id))
        media = db.get(MediaItemRow, task.media_item_id) if task is not None else None
        if media is not None:
            glossary = GlossaryService(db).prompt_block(
                scope_key=scope_key_for_media(media),
                target_language=row.target_language,
            )
    return locale, glossary
