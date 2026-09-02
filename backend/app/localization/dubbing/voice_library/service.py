"""CRUD and approval workflow for the series voice library."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EpisodeVoiceCastRow, MediaItemRow, VoiceCharacterRow, VoiceReferenceRow
from app.localization.dubbing.options import speaker_key
from app.localization.dubbing.voice_library.embeddings import cosine_similarity, embedding_from_wav, mean_embedding
from app.localization.dubbing.voice_library.paths import (
    character_dir,
    relative_reference_path,
    resolve_reference_path,
    series_voice_dir,
)


class VoiceLibraryError(ValueError):
    pass


@dataclass(frozen=True)
class CharacterVoiceBinding:
    character_id: int
    character_key: str
    display_name: str
    reference_relative_path: str
    reference_sha256: str
    voice_model: str
    cfg_weight: float | None
    synthesis_seed: int | None
    variant: str


def _character_key(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    return speaker_key(cleaned) or "character"


class VoiceLibraryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _cast_owner(self, media: MediaItemRow) -> MediaItemRow:
        if media.media_type != "episode":
            return media
        if media.parent_media_id:
            parent = self.db.get(MediaItemRow, media.parent_media_id)
            if parent is not None and parent.media_type == "series":
                return parent
        return media

    def list_characters(self, media: MediaItemRow, *, target_language: str) -> list[VoiceCharacterRow]:
        owner = self._cast_owner(media)
        return list(
            self.db.scalars(
                select(VoiceCharacterRow)
                .where(
                    VoiceCharacterRow.media_item_id == owner.id,
                    VoiceCharacterRow.target_language == target_language,
                )
                .order_by(VoiceCharacterRow.display_name)
            )
        )

    def get_character(self, character_id: int) -> VoiceCharacterRow | None:
        return self.db.get(VoiceCharacterRow, character_id)

    def upsert_character(
        self,
        media: MediaItemRow,
        *,
        target_language: str,
        display_name: str,
        character_key: str | None = None,
    ) -> VoiceCharacterRow:
        owner = self._cast_owner(media)
        key = _character_key(character_key or display_name)
        row = self.db.scalar(
            select(VoiceCharacterRow).where(
                VoiceCharacterRow.media_item_id == owner.id,
                VoiceCharacterRow.target_language == target_language,
                VoiceCharacterRow.character_key == key,
            )
        )
        if row is None:
            row = VoiceCharacterRow(
                media_item_id=owner.id,
                target_language=target_language,
                character_key=key,
                display_name=display_name.strip()[:120] or key,
            )
        else:
            row.display_name = display_name.strip()[:120] or row.display_name
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def add_reference_from_path(
        self,
        character: VoiceCharacterRow,
        *,
        source_path: str | Path,
        variant: str = "neutral",
        source_cue_indices: list[int] | None = None,
        make_canonical: bool = False,
    ) -> VoiceReferenceRow:
        absolute = Path(source_path)
        if not absolute.is_file():
            try:
                absolute = resolve_reference_path(str(source_path))
            except ValueError:
                raise VoiceLibraryError(f"Reference audio is missing: {source_path}") from None
        digest = hashlib.sha256(absolute.read_bytes()).hexdigest()
        owner_media = self.db.get(MediaItemRow, character.media_item_id)
        series_dir = series_voice_dir(
            character.media_item_id,
            slug=owner_media.title if owner_media else None,
        )
        dest_dir = character_dir(series_dir, character.character_key)
        dest = dest_dir / f"{variant}-{digest[:12]}.wav"
        if absolute.resolve() != dest.resolve():
            shutil.copyfile(absolute, dest)
        relative = relative_reference_path(dest)
        embedding = embedding_from_wav(dest)
        row = VoiceReferenceRow(
            character_id=character.id,
            variant=variant,
            relative_path=relative,
            sha256=digest,
            duration_s=None,
            source_cue_indices=source_cue_indices or [],
            approved=False,
            is_canonical=make_canonical,
            embedding_json=embedding,
        )
        if make_canonical:
            for existing in self.db.scalars(
                select(VoiceReferenceRow).where(VoiceReferenceRow.character_id == character.id)
            ):
                existing.is_canonical = False
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        self._refresh_character_centroid(character)
        return row

    def approve_reference(
        self,
        reference: VoiceReferenceRow,
        *,
        voice_model: str,
        cfg_weight: float | None = None,
        synthesis_seed: int | None = 0,
        make_canonical: bool = True,
    ) -> VoiceCharacterRow:
        character = self.db.get(VoiceCharacterRow, reference.character_id)
        if character is None:
            raise VoiceLibraryError("Character not found for this reference.")
        reference.approved = True
        if make_canonical:
            for existing in self.db.scalars(
                select(VoiceReferenceRow).where(VoiceReferenceRow.character_id == character.id)
            ):
                existing.is_canonical = existing.id == reference.id
            reference.is_canonical = True
        character.approved_voice_model = voice_model
        character.approval_status = "approved"
        character.synthesis_params_json = {
            "cfg_weight": cfg_weight,
            "seed": synthesis_seed,
            "variant": reference.variant,
            "reference_id": reference.id,
        }
        self.db.add(reference)
        self.db.add(character)
        self.db.commit()
        self.db.refresh(character)
        self._refresh_character_centroid(character)
        return character

    def _refresh_character_centroid(self, character: VoiceCharacterRow) -> None:
        embeddings = [
            row.embedding_json
            for row in self.db.scalars(
                select(VoiceReferenceRow).where(
                    VoiceReferenceRow.character_id == character.id,
                    VoiceReferenceRow.approved.is_(True),
                )
            )
            if isinstance(row.embedding_json, list) and row.embedding_json
        ]
        character.embedding_centroid_json = mean_embedding(embeddings) if embeddings else []
        self.db.add(character)
        self.db.commit()

    def canonical_reference(self, character: VoiceCharacterRow) -> VoiceReferenceRow | None:
        return self.db.scalar(
            select(VoiceReferenceRow)
            .where(
                VoiceReferenceRow.character_id == character.id,
                VoiceReferenceRow.is_canonical.is_(True),
                VoiceReferenceRow.approved.is_(True),
            )
            .order_by(VoiceReferenceRow.id.desc())
        )

    def binding_for_character(self, character: VoiceCharacterRow) -> CharacterVoiceBinding | None:
        reference = self.canonical_reference(character)
        if reference is None or character.approval_status != "approved":
            return None
        params = character.synthesis_params_json if isinstance(character.synthesis_params_json, dict) else {}
        return CharacterVoiceBinding(
            character_id=character.id,
            character_key=character.character_key,
            display_name=character.display_name,
            reference_relative_path=reference.relative_path,
            reference_sha256=reference.sha256,
            voice_model=character.approved_voice_model or "",
            cfg_weight=params.get("cfg_weight"),
            synthesis_seed=params.get("seed"),
            variant=reference.variant,
        )

    def bindings_for_episode(
        self,
        media: MediaItemRow,
        *,
        target_language: str,
    ) -> dict[str, CharacterVoiceBinding]:
        assignments = self.db.scalars(
            select(EpisodeVoiceCastRow).where(
                EpisodeVoiceCastRow.media_item_id == media.id,
                EpisodeVoiceCastRow.target_language == target_language,
                EpisodeVoiceCastRow.status == "assigned",
            )
        )
        bindings: dict[str, CharacterVoiceBinding] = {}
        for assignment in assignments:
            if assignment.character_id is None:
                continue
            character = self.get_character(assignment.character_id)
            if character is None:
                continue
            binding = self.binding_for_character(character)
            if binding is None:
                continue
            bindings[f"cue:{assignment.cue_index}"] = binding
            bindings[character.character_key] = binding
        return bindings

    def list_references(self, character_id: int) -> list[VoiceReferenceRow]:
        return list(
            self.db.scalars(
                select(VoiceReferenceRow)
                .where(VoiceReferenceRow.character_id == character_id)
                .order_by(VoiceReferenceRow.created_at.desc())
            )
        )

    def episode_cast(self, media: MediaItemRow, *, target_language: str) -> list[EpisodeVoiceCastRow]:
        return list(
            self.db.scalars(
                select(EpisodeVoiceCastRow)
                .where(
                    EpisodeVoiceCastRow.media_item_id == media.id,
                    EpisodeVoiceCastRow.target_language == target_language,
                )
                .order_by(EpisodeVoiceCastRow.cue_index)
            )
        )

    def has_episode_cast(self, media: MediaItemRow, *, target_language: str) -> bool:
        row = self.db.scalar(
            select(EpisodeVoiceCastRow.id)
            .where(
                EpisodeVoiceCastRow.media_item_id == media.id,
                EpisodeVoiceCastRow.target_language == target_language,
            )
            .limit(1)
        )
        return row is not None

    def has_approved_characters(self, media: MediaItemRow, *, target_language: str) -> bool:
        owner = self._cast_owner(media)
        row = self.db.scalar(
            select(VoiceCharacterRow.id)
            .where(
                VoiceCharacterRow.media_item_id == owner.id,
                VoiceCharacterRow.target_language == target_language,
                VoiceCharacterRow.approval_status == "approved",
            )
            .limit(1)
        )
        return row is not None

    def dub_readiness(self, media: MediaItemRow, *, target_language: str) -> tuple[bool, str]:
        cast_rows = self.episode_cast(media, target_language=target_language)
        if not cast_rows:
            if self.has_approved_characters(media, target_language=target_language):
                return False, "Run episode cue analysis before dubbing."
            return True, ""
        unresolved = self.unresolved_cue_count(media, target_language=target_language)
        if unresolved:
            return False, (
                f"{unresolved} subtitle cues still need an approved character voice before dubbing."
            )
        missing: list[str] = []
        for row in cast_rows:
            if row.character_id is None:
                missing.append(f"cue {row.cue_index}")
                continue
            character = self.get_character(row.character_id)
            if character is None or self.binding_for_character(character) is None:
                label = character.display_name if character else f"character {row.character_id}"
                missing.append(label)
        if missing:
            labels = ", ".join(sorted(set(missing))[:6])
            suffix = "…" if len(set(missing)) > 6 else ""
            return False, f"Approve a canonical voice reference for: {labels}{suffix}"
        return True, ""

    def assign_cue(
        self,
        media: MediaItemRow,
        *,
        target_language: str,
        cue_index: int,
        character_id: int | None,
    ) -> EpisodeVoiceCastRow:
        row = self.db.scalar(
            select(EpisodeVoiceCastRow).where(
                EpisodeVoiceCastRow.media_item_id == media.id,
                EpisodeVoiceCastRow.target_language == target_language,
                EpisodeVoiceCastRow.cue_index == cue_index,
            )
        )
        if row is None:
            row = EpisodeVoiceCastRow(
                media_item_id=media.id,
                target_language=target_language,
                cue_index=cue_index,
            )
        if character_id is None:
            row.character_id = None
            row.status = "unresolved"
            row.confidence = None
        else:
            character = self.get_character(character_id)
            if character is None:
                raise VoiceLibraryError("Character not found.")
            row.character_id = character.id
            row.status = "assigned"
            row.confidence = 1.0
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def adopt_reference_candidate(
        self,
        character: VoiceCharacterRow,
        *,
        relative_path: str,
        variant: str = "neutral",
        source_cue_indices: list[int] | None = None,
        make_canonical: bool = True,
    ) -> VoiceReferenceRow:
        absolute = resolve_reference_path(relative_path)
        return self.add_reference_from_path(
            character,
            source_path=absolute,
            variant=variant,
            source_cue_indices=source_cue_indices,
            make_canonical=make_canonical,
        )

    def unresolved_cue_count(self, media: MediaItemRow, *, target_language: str) -> int:
        rows = self.db.scalars(
            select(EpisodeVoiceCastRow).where(
                EpisodeVoiceCastRow.media_item_id == media.id,
                EpisodeVoiceCastRow.target_language == target_language,
                EpisodeVoiceCastRow.status.in_(("uncertain", "unresolved")),
            )
        )
        return len(list(rows))

    def match_character_by_embedding(
        self,
        media: MediaItemRow,
        *,
        target_language: str,
        embedding: list[float],
        threshold: float = 0.72,
    ) -> tuple[VoiceCharacterRow | None, float]:
        best: VoiceCharacterRow | None = None
        best_score = 0.0
        for character in self.list_characters(media, target_language=target_language):
            centroid = character.embedding_centroid_json
            if not isinstance(centroid, list) or not centroid:
                continue
            score = cosine_similarity(embedding, centroid)
            limit = character.similarity_threshold or threshold
            if score >= limit and score > best_score:
                best = character
                best_score = score
        return best, best_score

    def snapshot_bindings(self, bindings: dict[str, CharacterVoiceBinding]) -> dict[str, Any]:
        return {
            key: {
                "character_id": value.character_id,
                "character_key": value.character_key,
                "display_name": value.display_name,
                "reference_relative_path": value.reference_relative_path,
                "reference_sha256": value.reference_sha256,
                "voice_model": value.voice_model,
                "cfg_weight": value.cfg_weight,
                "synthesis_seed": value.synthesis_seed,
                "variant": value.variant,
            }
            for key, value in bindings.items()
        }
