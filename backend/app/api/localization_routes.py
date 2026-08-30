"""Media and localization-task API routes."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.schemas import (
    JobActionOut,
    JobOut,
    LanguageAvailabilityOut,
    LanguageCatalogOut,
    LocalizationTaskCreate,
    LocalizationTaskOut,
    MediaEnsureIn,
    MediaItemOut,
    MediaLocalizationOut,
    MediaRefOut,
    TaskAiSummaryOut,
    TranscribeCreate,
    DubCreate,
    VoiceCastOut,
    VoiceCastSuggestionOut,
    VoiceCastDraftUpdate,
    VoiceModelOptionOut,
    GlossaryEntryIn,
    GlossaryOut,
    GlossaryEntryOut,
)
from app.db import get_db, release_session_connection
from app.db.models import LocalizationTaskRow, MediaItemRow, VoiceCastDraftRow
from app.integrations.bazarr.client import BazarrError
from app.jobs.service import JobService, job_to_out
from app.jobs.worker import worker
from app.languages import LanguageNormalizationError, list_featured_languages, list_languages
from app.localization.planner import TaskPlanner
from app.localization.service import (
    ActiveTaskExistsError,
    LocalizationTaskService,
    UnsupportedCapabilityError,
)
from app.localization.dubbing.providers.chatterbox import (
    TTSError,
    recommended_voice_models_for_language,
    resolve_voice_model_for_language,
    resolve_voice_profile,
)
from app.localization.dubbing.voice_cast import (
    VoiceCastDraftService,
    VoiceCastError,
    VoiceCastService,
)
from app.media import MediaRef
from app.media.bazarr_provider import (
    BAZARR_PROVIDER_ID,
    BazarrMediaProvider,
    episode_external_id,
    movie_external_id,
)
from app.media.catalog import MediaCatalogError, configured_media_catalog
from app.media.service import MediaItemService
from app.services.settings import SettingsService
from app.subtitles.filenames import languages_compatible

router = APIRouter(prefix="/api")


def _episode_series_title(db: Session, row: MediaItemRow) -> str | None:
    """Resolve an episode's series title without changing movie metadata."""
    if row.media_type != "episode":
        return None

    if row.parent_media_id:
        parent = db.get(MediaItemRow, row.parent_media_id)
        if parent and parent.media_type == "series" and parent.title:
            return parent.title

    metadata = row.metadata_json or {}
    saved_title = metadata.get("series_title") if isinstance(metadata, dict) else None
    if isinstance(saved_title, str) and saved_title.strip():
        return saved_title.strip()

    title_match = re.match(r"^(.+?)\s+-\s+S\d{1,2}E\d{1,3}(?:\s+-|$)", row.title)
    if title_match:
        return title_match.group(1).strip()

    parts = [part for part in (row.path or "").replace("\\", "/").split("/") if part]
    for index, part in enumerate(parts):
        if index and re.fullmatch(r"(?:season|series)\s*\d+", part, flags=re.IGNORECASE):
            return parts[index - 1]
    return None


def _media_item_out(db: Session, row: MediaItemRow) -> MediaItemOut:
    return MediaItemOut(
        id=row.id,
        provider_id=row.provider_id,
        external_id=row.external_id,
        media_type=row.media_type,
        title=row.title,
        year=row.year,
        path=row.path,
        season=row.season,
        episode=row.episode,
        episode_title=row.episode_title,
        bazarr_movie_id=row.bazarr_movie_id,
        bazarr_series_id=row.bazarr_series_id,
        bazarr_episode_id=row.bazarr_episode_id,
        parent_media_id=row.parent_media_id,
        series_title=_episode_series_title(db, row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _voice_cast_out(row: VoiceCastDraftRow) -> VoiceCastOut:
    """Translate persisted casting JSON into the public editable draft shape."""
    suggestions: list[VoiceCastSuggestionOut] = []
    for raw in row.suggestions_json or []:
        if not isinstance(raw, dict):
            continue
        raw_cues = raw.get("cue_indices")
        cues = [int(item) for item in raw_cues if isinstance(item, int)] if isinstance(raw_cues, list) else []
        raw_voice_model = str(raw.get("voice_model") or "")
        try:
            voice_model = resolve_voice_profile(raw_voice_model, row.target_language).id
        except TTSError:
            voice_model = resolve_voice_model_for_language(row.target_language)
        suggestions.append(
            VoiceCastSuggestionOut(
                speaker_id=str(raw.get("speaker_id") or "Unidentified speaker"),
                voice_style=str(raw.get("voice_style") or "No voice style note"),
                cue_indices=cues,
                confidence=raw.get("confidence") if isinstance(raw.get("confidence"), (int, float)) else None,
                voice_model=voice_model,
                enabled=bool(raw.get("enabled", True)),
            )
        )
    return VoiceCastOut(
        id=row.id,
        media_item_id=row.media_item_id,
        target_language=row.target_language,
        provider_id=row.provider_id,
        model_id=row.model_id,
        suggestions=suggestions,
        analysed_cue_count=row.analysed_cue_count,
        metadata_used={
            str(key): value
            for key, value in (row.metadata_json or {}).items()
            if isinstance(value, (str, int))
        },
        mix_mode=row.mix_mode,
        available_voice_models=[
            VoiceModelOptionOut(id=model_id, label=label)
            for model_id, label in recommended_voice_models_for_language(row.target_language)
        ],
    )


def _progress_steps(task: LocalizationTaskRow, jobs: list) -> list[dict[str, str]]:
    """Authoritative checkpoints when present; otherwise infer from executions."""
    if (task.capability or "subtitles").lower() == "audio":
        return _audio_progress_steps(task, jobs)

    from app.localization.checkpoints import (
        has_checkpoint_data,
        progress_steps,
        read_checkpoints,
    )

    if has_checkpoint_data(task.metadata_json):
        steps = progress_steps(read_checkpoints(task.metadata_json))
        steps.insert(2, _transcription_progress_step(task, jobs))
        return steps

    kinds_done = {j.job_kind for j in jobs if j.status == "completed"}
    kinds_active = {j.job_kind for j in jobs if j.status in {"pending", "processing", "paused"}}
    kinds_failed = {j.job_kind for j in jobs if j.status == "failed"}

    def state(kind: str) -> str:
        if kind in kinds_done:
            return "done"
        if kind in kinds_active:
            return "active"
        if kind in kinds_failed:
            return "failed"
        return "pending"

    steps = [
        {"id": "source", "label": "Source found", "state": state("request")},
        {"id": "extract", "label": "Source extracted", "state": state("extract")},
        _transcription_progress_step(task, jobs),
        {"id": "translate", "label": "Translating", "state": state("translate")},
        {"id": "validate", "label": "Validating", "state": "pending"},
        {"id": "write", "label": "Writing subtitle", "state": "pending"},
        {"id": "sync", "label": "Bazarr sync", "state": "pending"},
        {"id": "verify", "label": "Verification", "state": "pending"},
    ]
    by_id = {step["id"]: step for step in steps}
    if "transcribe" in kinds_active:
        by_id["source"]["state"] = "active"
        if by_id["extract"]["state"] == "pending":
            by_id["extract"]["state"] = "skipped"
    if "transcribe" in kinds_done:
        by_id["source"]["state"] = "done"
        if by_id["extract"]["state"] == "pending":
            by_id["extract"]["state"] = "skipped"
    if "transcribe" in kinds_failed:
        by_id["source"]["state"] = "failed"
        if by_id["extract"]["state"] == "pending":
            by_id["extract"]["state"] = "skipped"
    if "translate" in kinds_done or "translate" in kinds_active:
        if by_id["source"]["state"] == "pending":
            by_id["source"]["state"] = "done"
        if by_id["extract"]["state"] == "pending" and "extract" not in kinds_failed:
            by_id["extract"]["state"] = "skipped"
    if "translate" in kinds_done:
        by_id["translate"]["state"] = "done"
        by_id["validate"]["state"] = "done"
        by_id["write"]["state"] = "done"
        if task.status == "verifying":
            by_id["sync"]["state"] = "active"
            by_id["verify"]["state"] = "active"
        elif task.status == "completed":
            by_id["sync"]["state"] = "done"
            by_id["verify"]["state"] = "done"
        elif any(j.reason_code == "bazarr_verify_failed" for j in jobs if j.job_kind == "translate"):
            by_id["sync"]["state"] = "done"
            by_id["verify"]["state"] = "failed"
    if task.status == "completed":
        for step in steps:
            if step["state"] == "pending":
                step["state"] = "skipped" if step["id"] == "extract" else "done"
    if task.status == "waiting_for_source":
        request_failed = any(
            j.job_kind == "request" and j.reason_code == "not_found" for j in jobs
        )
        by_id["source"]["state"] = "failed" if request_failed else "active"
        if by_id["extract"]["state"] == "pending":
            by_id["extract"]["state"] = "skipped"
    if task.status == "failed" and task.error_code in {"not_found", "source_unavailable"}:
        by_id["source"]["state"] = "failed"
        if by_id["extract"]["state"] == "pending":
            by_id["extract"]["state"] = "skipped"
    return steps


def _transcription_progress_step(
    task: LocalizationTaskRow, jobs: list
) -> dict[str, str]:
    """Expose transcription as its own live-work stage badge."""
    job = next((item for item in reversed(jobs) if item.job_kind == "transcribe"), None)
    if job is not None:
        if job.status in {"pending", "processing", "paused"}:
            state = "active"
        elif job.status == "completed":
            state = "done"
        elif job.status == "failed":
            state = "failed"
        else:
            state = "skipped"
    elif task.status == "completed" or any(
        item.job_kind == "translate" for item in jobs
    ):
        state = "skipped"
    else:
        state = "pending"
    return {"id": "transcribe", "label": "Transcribing", "state": state}


def _audio_progress_steps(task: LocalizationTaskRow, jobs: list) -> list[dict[str, str]]:
    """Return live progress that reflects the actual dubbing pipeline."""
    steps = [
        {"id": "subtitles", "label": "Localized subtitles", "state": "pending"},
        {"id": "voice", "label": "Preparing voice", "state": "pending"},
        {"id": "speech", "label": "Generating speech", "state": "pending"},
        {"id": "mix", "label": "Mixing and saving dub", "state": "pending"},
        {"id": "verify", "label": "Checking output", "state": "pending"},
    ]
    by_id = {step["id"]: step for step in steps}
    job = next((item for item in reversed(jobs) if item.job_kind == "dub"), None)

    if task.status == "completed":
        for step in steps:
            step["state"] = "done"
        return steps

    if job is None:
        by_id["subtitles"]["state"] = (
            "failed"
            if task.status == "blocked" and task.error_code == "subtitle_missing"
            else "active"
        )
        return steps

    by_id["subtitles"]["state"] = "done"
    detail = (job.progress_detail or "").lower()
    progress = job.progress or 0

    if job.status == "completed":
        for step in steps:
            step["state"] = "done"
        return steps

    if job.status in {"failed", "cancelled", "skipped"}:
        failed_step = "voice"
        if "synthesizing" in detail or progress > 5:
            failed_step = "speech"
        if progress >= 90:
            failed_step = "mix"
        by_id[failed_step]["state"] = "failed"
        return steps

    if job.status == "pending" or (progress <= 5 and "synthesizing" not in detail):
        by_id["voice"]["state"] = "active"
        return steps

    by_id["voice"]["state"] = "done"
    if progress >= 90:
        by_id["speech"]["state"] = "done"
        by_id["mix"]["state"] = "active"
    else:
        by_id["speech"]["state"] = "active"
    return steps


def _task_out(
    db: Session,
    task: LocalizationTaskRow,
    *,
    include_detail: bool = False,
) -> LocalizationTaskOut:
    media = task.media_item
    jobs = LocalizationTaskService(db).jobs_for_task(task.id) if include_detail else []
    ai = None
    if include_detail:
        summary = LocalizationTaskService(db).ai_summary(task.id)
        ai = TaskAiSummaryOut(**summary)
    return LocalizationTaskOut(
        id=task.id,
        media_item_id=task.media_item_id,
        media_title=media.title if media else None,
        media_type=media.media_type if media else None,
        media_year=media.year if media else None,
        target_language_code=task.target_language_code,
        target_language_name=task.target_language_name,
        capability=task.capability,
        status=task.status,
        substate=task.substate,
        origin=task.origin,
        priority=task.priority,
        requested_by=task.requested_by,
        error_code=task.error_code,
        error_message=task.error_message,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        updated_at=task.updated_at,
        executions=[job_to_out(j) for j in jobs],
        ai=ai,
        progress_steps=_progress_steps(task, jobs) if include_detail else [],
        draft_subtitle_path=(task.metadata_json or {}).get("draft_subtitle_path")
        if isinstance(task.metadata_json, dict)
        else None,
    )


def _bazarr_provider(db: Session) -> BazarrMediaProvider:
    from app.integrations.bazarr.client import BazarrClient

    settings = SettingsService(db)
    url, key = settings.get_bazarr_credentials()
    if not url:
        raise HTTPException(status_code=400, detail="Bazarr URL is not configured")
    return BazarrMediaProvider(BazarrClient(url, key))


@router.get("/languages", response_model=list[LanguageCatalogOut])
def get_languages() -> list[LanguageCatalogOut]:
    return [
        LanguageCatalogOut(
            code=lang.code,
            display_name=lang.display_name,
            aliases=list(lang.aliases),
            region=lang.region,
            flag=lang.flag,
        )
        for lang in list_languages()
    ]


@router.get("/media/search", response_model=list[MediaRefOut])
async def search_media(
    q: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
) -> list[MediaRefOut]:
    try:
        catalog = configured_media_catalog(db)
        release_session_connection(db)
        _source, refs = await catalog.search(q)
    except MediaCatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        MediaRefOut(
            provider_id=r.provider_id,
            external_id=r.external_id,
            media_type=r.media_type,
            title=r.title,
            year=r.year,
            season=r.season,
            episode=r.episode,
            episode_title=r.episode_title,
            path=r.path,
            parent_external_id=r.parent_external_id,
            bazarr_movie_id=r.bazarr_movie_id,
            bazarr_series_id=r.bazarr_series_id,
            bazarr_episode_id=r.bazarr_episode_id,
        )
        for r in refs
    ]


@router.get("/media", response_model=list[MediaItemOut])
def list_media(
    response: Response,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[MediaItemOut]:
    svc = MediaItemService(db)
    response.headers["X-Total-Count"] = str(svc.count_items())
    return [_media_item_out(db, row) for row in svc.list_items(limit=limit, offset=offset)]


@router.post("/media", response_model=MediaItemOut)
async def ensure_media(payload: MediaEnsureIn, db: Session = Depends(get_db)) -> MediaItemOut:
    """Persist a media identity from search results or candidate fields."""
    media_svc = MediaItemService(db)
    external_id = payload.external_id
    if not external_id:
        if payload.bazarr_movie_id is not None:
            external_id = movie_external_id(payload.bazarr_movie_id)
        elif payload.bazarr_episode_id is not None:
            external_id = episode_external_id(payload.bazarr_episode_id)
        else:
            raise HTTPException(status_code=400, detail="external_id or Bazarr IDs required")

    # Prefer live metadata from the catalog that produced the selection.
    ref: MediaRef | None = None
    catalog = configured_media_catalog(db)
    release_session_connection(db)
    ref = await catalog.get(payload.provider_id or BAZARR_PROVIDER_ID, external_id)

    if ref is None:
        if not payload.title or not payload.media_type:
            raise HTTPException(status_code=404, detail="Media not found")
        ref = MediaRef(
            provider_id=payload.provider_id or BAZARR_PROVIDER_ID,
            external_id=external_id,
            media_type=payload.media_type,
            title=payload.title,
            year=payload.year,
            season=payload.season,
            episode=payload.episode,
            episode_title=payload.episode_title,
            path=payload.path,
            parent_external_id=payload.parent_external_id,
            bazarr_movie_id=payload.bazarr_movie_id,
            bazarr_series_id=payload.bazarr_series_id,
            bazarr_episode_id=payload.bazarr_episode_id,
        )
    row = media_svc.upsert_from_ref(ref)
    return _media_item_out(db, row)


@router.get("/media/{media_id}", response_model=MediaItemOut)
def get_media(media_id: int, db: Session = Depends(get_db)) -> MediaItemOut:
    row = MediaItemService(db).get(media_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return _media_item_out(db, row)


@router.get("/media/{media_id}/localization", response_model=MediaLocalizationOut)
async def get_media_localization(
    media_id: int,
    db: Session = Depends(get_db),
) -> MediaLocalizationOut:
    media_svc = MediaItemService(db)
    row = media_svc.get(media_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Media not found")
    ref = media_svc.to_ref(row)

    languages: list[LanguageAvailabilityOut] = []
    try:
        provider = _bazarr_provider(db)
        release_session_connection(db)
        state = await provider.get_localization_state(ref)
        for item in state.languages:
            languages.append(
                LanguageAvailabilityOut(
                    language_code=item.language_code,
                    language_name=item.language_name,
                    available=item.available,
                )
            )
    except (BazarrError, HTTPException):
        # Fall back to catalog languages with unknown availability.
        for lang in list_featured_languages():
            languages.append(
                LanguageAvailabilityOut(
                    language_code=lang.code,
                    language_name=lang.display_name,
                    available=False,
                )
            )

    # Overlay a single current task per language: availability + active + latest.
    task_svc = LocalizationTaskService(db)
    for lang in languages:
        overlay = task_svc.latest_task_for_language(media_id, lang.language_code)
        if overlay is None:
            continue
        lang.task_status = overlay.status
        lang.task_id = overlay.id
        lang.task_substate = overlay.substate

    # Languages that have tasks but aren't in the availability list yet.
    seen_codes = {lang.language_code for lang in languages}
    for task in task_svc.list_tasks(media_item_id=media_id, capability="subtitles", limit=200):
        if task.media_item_id != media_id or task.capability != "subtitles":
            continue
        if any(languages_compatible(task.target_language_code, code) for code in seen_codes):
            continue
        overlay = task_svc.latest_task_for_language(media_id, task.target_language_code)
        if overlay is None or overlay.id != task.id:
            continue
        languages.append(
            LanguageAvailabilityOut(
                language_code=task.target_language_code,
                language_name=task.target_language_name,
                available=task.status == "completed",
                task_status=overlay.status,
                task_id=overlay.id,
                task_substate=overlay.substate,
            )
        )
        seen_codes.add(task.target_language_code)

    gate = await JobService(db).transcribe_gate_for_media(row)
    dub_gate = await JobService(db).dub_gate_for_media(
        row,
        target_language=SettingsService(db).get_public().target_language.code,
    )
    return MediaLocalizationOut(
        media_id=media_id,
        capability="subtitles",
        languages=languages,
        can_transcribe=gate.can_transcribe,
        transcribe_reason=gate.reason,
        can_dub=dub_gate.can_dub,
        dub_reason=dub_gate.reason,
    )


@router.get("/media/{media_id}/actions", response_model=list[JobActionOut])
def get_media_actions(media_id: int, db: Session = Depends(get_db)) -> list[JobActionOut]:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return JobService(db).list_job_actions_for_media(media)


@router.post("/media/{media_id}/transcribe", response_model=JobOut)
async def transcribe_media(
    media_id: int,
    payload: TranscribeCreate | None = None,
    db: Session = Depends(get_db),
) -> JobOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    body = payload or TranscribeCreate()
    try:
        return await JobService(db).start_manual_transcribe(
            media,
            target_language=body.target_language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/media/{media_id}/dub", response_model=JobOut)
async def dub_media(
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


@router.post("/media/{media_id}/dub/voice-cast", response_model=VoiceCastOut)
async def suggest_dub_voice_cast(
    media_id: int,
    payload: DubCreate | None = None,
    db: Session = Depends(get_db),
) -> VoiceCastOut:
    """Analyse source audio and replace the saved editable casting draft."""
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    target_language = (payload.target_language if payload else None) or "pt-PT"
    try:
        result = await VoiceCastService(db).suggest(media, target_language=target_language)
        draft = VoiceCastDraftService(db).save_analysis(
            media,
            target_language=target_language,
            mix_mode=payload.mix_mode if payload else "background_preserved",
            result=result,
        )
        return _voice_cast_out(draft)
    except VoiceCastError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/media/{media_id}/dub/voice-cast", response_model=VoiceCastOut)
def get_dub_voice_cast(
    media_id: int,
    target_language: str = Query("pt-PT"),
    db: Session = Depends(get_db),
) -> VoiceCastOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    draft = VoiceCastDraftService(db).get(media_id, target_language)
    if draft is None:
        raise HTTPException(status_code=404, detail="No saved voice-casting draft for this language.")
    return _voice_cast_out(draft)


@router.put("/media/{media_id}/dub/voice-cast", response_model=VoiceCastOut)
def update_dub_voice_cast(
    media_id: int,
    payload: VoiceCastDraftUpdate,
    target_language: str = Query("pt-PT"),
    db: Session = Depends(get_db),
) -> VoiceCastOut:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    drafts = VoiceCastDraftService(db)
    draft = drafts.get(media_id, target_language)
    if draft is None:
        raise HTTPException(status_code=404, detail="No saved voice-casting draft for this language.")
    try:
        saved = drafts.update(
            draft,
            suggestions=[suggestion.model_dump() for suggestion in payload.suggestions],
            mix_mode=payload.mix_mode,
        )
    except VoiceCastError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _voice_cast_out(saved)


@router.post("/media/{media_id}/dub/voice-cast/request", response_model=JobOut)
async def request_dub_from_voice_cast(
    media_id: int,
    target_language: str = Query("pt-PT"),
    db: Session = Depends(get_db),
) -> JobOut:
    """Start a dub using the saved, reviewed voice-casting draft."""
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    drafts = VoiceCastDraftService(db)
    draft = drafts.get(media_id, target_language)
    if draft is None:
        raise HTTPException(status_code=404, detail="No saved voice-casting draft for this language.")
    try:
        return await JobService(db).start_manual_dub(
            media,
            target_language=draft.target_language,
            replace_existing=True,
            mix_mode=draft.mix_mode,
            speaker_voice_overrides=drafts.speaker_voice_overrides(draft),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/media/{media_id}/localization-tasks", response_model=LocalizationTaskOut)
async def create_media_localization_task(
    media_id: int,
    payload: LocalizationTaskCreate,
    db: Session = Depends(get_db),
) -> LocalizationTaskOut | JSONResponse:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")

    capability = (payload.capability or "subtitles").strip().lower()
    try:
        task, created = LocalizationTaskService(db).create_manual_task(
            media_item=media,
            target_language=payload.target_language,
            capability=capability,
            requested_by="user",
        )
    except UnsupportedCapabilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LanguageNormalizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ActiveTaskExistsError as exc:
        return JSONResponse(
            status_code=409,
            content={"error": "active_task_exists", "task_id": exc.task_id, "detail": str(exc)},
        )

    await TaskPlanner(db).plan(task.id)
    task = LocalizationTaskService(db).get(task.id)
    assert task is not None
    return _task_out(db, task, include_detail=True)


@router.get("/localization-tasks", response_model=list[LocalizationTaskOut])
def list_localization_tasks(
    response: Response,
    status: str | None = None,
    origin: str | None = None,
    capability: str | None = None,
    language: str | None = None,
    media_type: str | None = None,
    media_item_id: int | None = None,
    active_only: bool = False,
    include_detail: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: Literal["created_at", "completed_at"] = "created_at",
    db: Session = Depends(get_db),
) -> list[LocalizationTaskOut]:
    svc = LocalizationTaskService(db)
    total = svc.count_tasks(
        status=status,
        origin=origin,
        capability=capability,
        language=language,
        media_type=media_type,
        media_item_id=media_item_id,
        active_only=active_only,
    )
    rows = svc.list_tasks(
        status=status,
        origin=origin,
        capability=capability,
        language=language,
        media_type=media_type,
        media_item_id=media_item_id,
        limit=limit,
        offset=offset,
        active_only=active_only,
        sort=sort,
    )
    response.headers["X-Total-Count"] = str(total)
    return [_task_out(db, row, include_detail=include_detail) for row in rows]


@router.get("/localization-tasks/{task_id}", response_model=LocalizationTaskOut)
def get_localization_task(task_id: int, db: Session = Depends(get_db)) -> LocalizationTaskOut:
    task = LocalizationTaskService(db).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_out(db, task, include_detail=True)


@router.post("/localization-tasks/{task_id}/retry", response_model=LocalizationTaskOut)
async def retry_localization_task(
    task_id: int,
    db: Session = Depends(get_db),
) -> LocalizationTaskOut:
    try:
        task = LocalizationTaskService(db).prepare_retry(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await TaskPlanner(db).plan(task.id)
    task = LocalizationTaskService(db).get(task.id)
    assert task is not None
    return _task_out(db, task, include_detail=True)


@router.post("/localization-tasks/{task_id}/cancel", response_model=LocalizationTaskOut)
def cancel_localization_task(task_id: int, db: Session = Depends(get_db)) -> LocalizationTaskOut:
    try:
        task = LocalizationTaskService(db).cancel(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for job in LocalizationTaskService(db).jobs_for_task(task.id):
        if job.status == "cancelled":
            worker.cancel_job(job.id)
    return _task_out(db, task, include_detail=True)


@router.post("/localization-tasks/{task_id}/approve", response_model=LocalizationTaskOut)
async def approve_localization_task(
    task_id: int,
    db: Session = Depends(get_db),
) -> LocalizationTaskOut:
    import shutil
    from pathlib import Path

    from app.jobs.translate import draft_subtitle_path

    task = LocalizationTaskService(db).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "awaiting_approval":
        raise HTTPException(status_code=400, detail="Task is not awaiting approval")
    jobs = LocalizationTaskService(db).jobs_for_task(task.id)
    translate = next(
        (
            j
            for j in reversed(jobs)
            if (j.job_kind or "translate") == "translate" and j.status == "completed"
        ),
        None,
    )
    if translate is None:
        raise HTTPException(status_code=400, detail="No completed translation to approve")
    meta = dict(task.metadata_json or {})
    draft = Path(
        str(meta.get("draft_subtitle_path") or draft_subtitle_path(Path(translate.target_subtitle_path)))
    )
    target = Path(translate.target_subtitle_path)
    if not draft.is_file():
        raise HTTPException(status_code=400, detail="Draft subtitle file is missing")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(draft, target)
    LocalizationTaskService(db).transition(
        task, "verifying", substate="bazarr_sync", clear_error=True
    )
    await TaskPlanner(db).plan(task.id)
    task = LocalizationTaskService(db).get(task.id)
    assert task is not None
    return _task_out(db, task, include_detail=True)


@router.get("/media/{media_id}/glossary", response_model=GlossaryOut)
def get_media_glossary(
    media_id: int,
    language: str = Query(..., min_length=2),
    db: Session = Depends(get_db),
) -> GlossaryOut:
    from app.localization.glossary import GlossaryService, scope_key_for_media

    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    scope = scope_key_for_media(media)
    entries = GlossaryService(db).list_entries(scope_key=scope, target_language=language)
    return GlossaryOut(
        scope_key=scope,
        target_language=language,
        entries=[
            GlossaryEntryOut(id=row.id, source=row.source, target=row.target, locked=row.locked)
            for row in entries
        ],
    )


@router.put("/media/{media_id}/glossary", response_model=GlossaryOut)
def put_media_glossary(
    media_id: int,
    language: str = Query(..., min_length=2),
    payload: list[GlossaryEntryIn] | None = None,
    db: Session = Depends(get_db),
) -> GlossaryOut:
    from app.localization.glossary import GlossaryService, scope_key_for_media

    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    scope = scope_key_for_media(media)
    saved = GlossaryService(db).replace_entries(
        scope_key=scope,
        target_language=language,
        entries=[item.model_dump() for item in (payload or [])],
    )
    return GlossaryOut(
        scope_key=scope,
        target_language=language,
        entries=[
            GlossaryEntryOut(id=row.id, source=row.source, target=row.target, locked=row.locked)
            for row in saved
        ],
    )
