"""Series voice library and per-episode cue casting API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.schemas import (
    AuditionCandidateOut,
    DubCreate,
    JobOut,
    VoiceAuditionOut,
    VoiceCharacterCreate,
    VoiceCharacterOut,
    VoiceCueAssignBatchIn,
    VoiceLibraryOut,
    VoiceModelOptionOut,
    VoiceReferenceAdoptIn,
    VoiceReferenceApproveIn,
    VoiceReferenceOut,
    EpisodeCueCastOut,
    ReferenceCandidateOut,
)
from app.db import get_db
from app.db.models import VoiceCharacterRow, VoiceReferenceRow
from app.jobs.service import JobService
from app.localization.dubbing.providers.chatterbox import (
    TTSError,
    recommended_voice_models_for_language,
    resolve_voice_model_for_language,
)
from app.localization.dubbing.voice_cast import VoiceCastDraftService
from app.localization.dubbing.voice_library.analysis import SpeakerAnalysisService, VoiceLibraryError
from app.localization.dubbing.voice_library.audition import VoiceAuditionService
from app.localization.dubbing.voice_library.paths import resolve_reference_path, voices_root
from app.localization.dubbing.voice_library.service import VoiceLibraryService
from app.media.service import MediaItemService

router = APIRouter(prefix="/api")


def _reference_out(row: VoiceReferenceRow) -> VoiceReferenceOut:
    cues = row.source_cue_indices if isinstance(row.source_cue_indices, list) else []
    return VoiceReferenceOut(
        id=row.id,
        variant=row.variant,
        relative_path=row.relative_path,
        sha256=row.sha256,
        approved=bool(row.approved),
        is_canonical=bool(row.is_canonical),
        source_cue_indices=[int(item) for item in cues if isinstance(item, int)],
    )


def _character_out(library: VoiceLibraryService, row: VoiceCharacterRow) -> VoiceCharacterOut:
    params = row.synthesis_params_json if isinstance(row.synthesis_params_json, dict) else {}
    return VoiceCharacterOut(
        id=row.id,
        character_key=row.character_key,
        display_name=row.display_name,
        approval_status=row.approval_status,
        approved_voice_model=row.approved_voice_model,
        synthesis_params=params,
        references=[_reference_out(reference) for reference in library.list_references(row.id)],
    )


def _voice_library_out(
    db: Session,
    media_id: int,
    *,
    target_language: str,
    mix_mode: str = "background_preserved",
) -> VoiceLibraryOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    library = VoiceLibraryService(db)
    ready, reason = library.dub_readiness(media, target_language=target_language)
    cast_rows = library.episode_cast(media, target_language=target_language)
    episode_cast: list[EpisodeCueCastOut] = []
    for row in cast_rows:
        character = library.get_character(row.character_id) if row.character_id else None
        episode_cast.append(
            EpisodeCueCastOut(
                cue_index=row.cue_index,
                character_id=row.character_id,
                character_key=character.character_key if character else None,
                display_name=character.display_name if character else None,
                speaker_label=row.speaker_label,
                confidence=row.confidence,
                status=row.status,
            )
        )
    models = [
        VoiceModelOptionOut(id=item.id, label=item.label)
        for item in recommended_voice_models_for_language(target_language)
    ]
    return VoiceLibraryOut(
        media_item_id=media.id,
        target_language=target_language,
        characters=[_character_out(library, character) for character in library.list_characters(media, target_language=target_language)],
        episode_cast=episode_cast,
        unresolved_cue_count=library.unresolved_cue_count(media, target_language=target_language),
        dub_ready=ready,
        dub_ready_reason=reason,
        available_voice_models=models,
        mix_mode=mix_mode if mix_mode in {"background_preserved", "voiceover_preview"} else "background_preserved",
    )


@router.get("/media/{media_id}/voice-library", response_model=VoiceLibraryOut)
def get_voice_library(
    media_id: int,
    target_language: str = Query("pt-PT"),
    db: Session = Depends(get_db),
) -> VoiceLibraryOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    draft = VoiceCastDraftService(db).get_for_media(media, target_language)
    mix_mode = draft.mix_mode if draft is not None else "background_preserved"
    return _voice_library_out(db, media_id, target_language=target_language, mix_mode=mix_mode)


@router.post("/media/{media_id}/voice-library/analyse", response_model=VoiceLibraryOut)
async def analyse_voice_library(
    media_id: int,
    payload: DubCreate | None = None,
    db: Session = Depends(get_db),
) -> VoiceLibraryOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    target_language = (payload.target_language if payload else None) or "pt-PT"
    mix_mode = payload.mix_mode if payload else "background_preserved"
    try:
        await SpeakerAnalysisService(db).assign_episode_cues(media, target_language=target_language)
    except VoiceLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _voice_library_out(db, media_id, target_language=target_language, mix_mode=mix_mode)


@router.post("/media/{media_id}/voice-library/reference-candidates", response_model=list[ReferenceCandidateOut])
async def build_reference_candidates(
    media_id: int,
    target_language: str = Query("pt-PT"),
    db: Session = Depends(get_db),
) -> list[ReferenceCandidateOut]:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    try:
        candidates = await SpeakerAnalysisService(db).build_reference_candidates(
            media,
            target_language=target_language,
        )
    except VoiceLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [
        ReferenceCandidateOut(
            character_key=item.character_key,
            display_name=item.display_name,
            cue_indices=item.cue_indices,
            relative_path=item.relative_path,
            confidence=item.confidence,
        )
        for item in candidates
    ]


@router.post("/media/{media_id}/voice-library/characters", response_model=VoiceCharacterOut)
def create_voice_character(
    media_id: int,
    payload: VoiceCharacterCreate,
    target_language: str = Query("pt-PT"),
    db: Session = Depends(get_db),
) -> VoiceCharacterOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    library = VoiceLibraryService(db)
    character = library.upsert_character(
        media,
        target_language=target_language,
        display_name=payload.display_name,
        character_key=payload.character_key,
    )
    return _character_out(library, character)


@router.post("/media/{media_id}/voice-library/characters/{character_id}/adopt-reference", response_model=VoiceReferenceOut)
def adopt_voice_reference(
    media_id: int,
    character_id: int,
    payload: VoiceReferenceAdoptIn,
    db: Session = Depends(get_db),
) -> VoiceReferenceOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    library = VoiceLibraryService(db)
    character = library.get_character(character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found.")
    try:
        reference = library.adopt_reference_candidate(
            character,
            relative_path=payload.relative_path,
            variant=payload.variant,
            source_cue_indices=payload.source_cue_indices,
            make_canonical=payload.make_canonical,
        )
    except VoiceLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _reference_out(reference)


@router.post("/media/{media_id}/voice-library/characters/{character_id}/audition", response_model=VoiceAuditionOut)
async def audition_voice_character(
    media_id: int,
    character_id: int,
    target_language: str = Query("pt-PT"),
    voice_model: str | None = Query(None),
    db: Session = Depends(get_db),
) -> VoiceAuditionOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    library = VoiceLibraryService(db)
    character = library.get_character(character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found.")
    reference = library.canonical_reference(character)
    if reference is None:
        references = library.list_references(character_id)
        reference = references[0] if references else None
    if reference is None:
        raise HTTPException(status_code=422, detail="Add a reference clip before auditioning.")
    model = voice_model or character.approved_voice_model or resolve_voice_model_for_language(target_language)
    try:
        matrix = await VoiceAuditionService(target_language=target_language).render_matrix(
            reference_relative_path=reference.relative_path,
            voice_model=model,
            seeds=(0,),
        )
    except TTSError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return VoiceAuditionOut(
        reference_sha256=matrix.reference_sha256,
        target_language=matrix.target_language,
        candidates=[
            AuditionCandidateOut(
                line_id=item.line_id,
                cfg_weight=item.cfg_weight,
                exaggeration=item.exaggeration,
                seed=item.seed,
                wav_path=item.wav_path,
                duration=item.duration,
                profile_id=item.profile_id,
            )
            for item in matrix.candidates
        ],
    )


@router.post("/media/{media_id}/voice-library/characters/{character_id}/approve", response_model=VoiceCharacterOut)
def approve_voice_character(
    media_id: int,
    character_id: int,
    payload: VoiceReferenceApproveIn,
    db: Session = Depends(get_db),
) -> VoiceCharacterOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    library = VoiceLibraryService(db)
    character = library.get_character(character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found.")
    reference = db.get(VoiceReferenceRow, payload.reference_id)
    if reference is None or reference.character_id != character.id:
        raise HTTPException(status_code=404, detail="Reference not found for this character.")
    try:
        character = library.approve_reference(
            reference,
            voice_model=payload.voice_model,
            cfg_weight=payload.cfg_weight,
            synthesis_seed=payload.synthesis_seed,
            make_canonical=payload.make_canonical,
        )
    except VoiceLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _character_out(library, character)


@router.put("/media/{media_id}/voice-library/cues", response_model=VoiceLibraryOut)
def update_voice_cues(
    media_id: int,
    payload: VoiceCueAssignBatchIn,
    target_language: str = Query("pt-PT"),
    db: Session = Depends(get_db),
) -> VoiceLibraryOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    library = VoiceLibraryService(db)
    for assignment in payload.assignments:
        try:
            library.assign_cue(
                media,
                target_language=target_language,
                cue_index=assignment.cue_index,
                character_id=assignment.character_id,
            )
        except VoiceLibraryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _voice_library_out(db, media_id, target_language=target_language)


@router.post("/media/{media_id}/voice-library/request-dub", response_model=JobOut)
async def request_dub_from_voice_library(
    media_id: int,
    payload: DubCreate | None = None,
    db: Session = Depends(get_db),
) -> JobOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    body = payload or DubCreate()
    try:
        return await JobService(db).start_manual_dub(
            media,
            target_language=body.target_language,
            replace_existing=body.replace_existing,
            mix_mode=body.mix_mode,
            speaker_voice_overrides=body.speaker_voices,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/media/{media_id}/voice-library/audio")
def stream_voice_library_audio(
    media_id: int,
    path: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
) -> FileResponse:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    del db
    try:
        resolved = resolve_reference_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found.")
    return FileResponse(resolved, media_type="audio/wav", filename=resolved.name)


@router.get("/media/{media_id}/voice-library/audition-audio")
def stream_audition_audio(
    media_id: int,
    file: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
) -> FileResponse:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    del media, db
    candidate = Path(file)
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Audition clip not found.")
    # Audition cache lives under config/cache/voice-auditions; reject path traversal.
    root = voices_root().resolve().parent / "cache" / "voice-auditions"
    resolved = candidate.resolve()
    if root not in resolved.parents:
        raise HTTPException(status_code=400, detail="Audition path is outside the cache directory.")
    return FileResponse(resolved, media_type="audio/wav", filename=resolved.name)
