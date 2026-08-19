"""Locale-specific translator notes (data, not hardcoded ifs)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import LocalePromptNoteRow

SEEDED_NOTES: dict[str, str] = {
    "pt-PT": (
        "Use European Portuguese (PT-PT), not Brazilian Portuguese (PT-BR). "
        "Prefer European vocabulary, grammar, spelling, and conversational usage "
        "(e.g. autocarro, ecrã, pequeno-almoço)."
    ),
    "pt-BR": (
        "Use Brazilian Portuguese (PT-BR), not European Portuguese. "
        "Prefer Brazilian vocabulary and spelling (e.g. ônibus, tela, café da manhã)."
    ),
    "pt": "Use generic Portuguese. Do not mix PT-PT and PT-BR forms in the same cue.",
    "fr-CA": "Use Canadian French (Québec). Prefer local vocabulary over metropolitan French where they differ.",
    "fr-FR": "Use metropolitan French (France).",
    "es-MX": "Use Mexican Spanish. Prefer Latin American usage over Peninsular Spanish.",
    "es-ES": "Use Peninsular Spanish (Spain), including vosotros where natural in dialogue.",
    "es-419": "Use Neutral Latin American Spanish.",
    "zh-CN": "Use Simplified Chinese.",
    "zh-TW": "Use Traditional Chinese (Taiwan).",
    "en-GB": "Use British English spelling and vocabulary.",
    "en-US": "Use American English spelling and vocabulary.",
}


def note_for(language_code: str, db: Session | None = None) -> str:
    code = (language_code or "").strip()
    if not code:
        return ""
    if db is not None:
        row = db.get(LocalePromptNoteRow, code)
        if row is None:
            row = db.get(LocalePromptNoteRow, code.lower())
        if row is not None and row.notes.strip():
            return row.notes.strip()
    if code in SEEDED_NOTES:
        return SEEDED_NOTES[code]
    lowered = code.lower()
    if lowered in SEEDED_NOTES:
        return SEEDED_NOTES[lowered]
    return ""


def seed_default_notes(db: Session) -> None:
    for code, notes in SEEDED_NOTES.items():
        if db.get(LocalePromptNoteRow, code) is None:
            db.add(LocalePromptNoteRow(language_code=code, notes=notes))
    db.commit()

