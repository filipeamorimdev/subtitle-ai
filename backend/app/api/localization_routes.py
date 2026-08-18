"""Media and localization-task API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.schemas import (
    JobActionOut,
    LanguageAvailabilityOut,
    LanguageCatalogOut,
    LocalizationTaskCreate,
    LocalizationTaskOut,
    MediaEnsureIn,
    MediaItemOut,
    MediaLocalizationOut,
    MediaRefOut,
    TaskAiSummaryOut,
)
from app.db import get_db, release_session_connection
from app.db.models import LocalizationTaskRow, MediaItemRow
from app.integrations.bazarr.client import BazarrError
from app.jobs.service import JobService, job_to_out
from app.jobs.worker import worker
from app.languages import LanguageNormalizationError, list_featured_languages, list_languages, normalize_language
from app.localization.planner import TaskPlanner
from app.localization.service import (
    ActiveTaskExistsError,
    LocalizationTaskService,
    UnsupportedCapabilityError,
)
from app.media import MediaRef
from app.media.bazarr_provider import (
    BAZARR_PROVIDER_ID,
    BazarrMediaProvider,
    episode_external_id,
    movie_external_id,
)
from app.media.service import MediaItemService
from app.services.settings import SettingsService
from app.subtitles.filenames import languages_compatible

router = APIRouter(prefix="/api")


def _media_item_out(row: MediaItemRow) -> MediaItemOut:
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
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _progress_steps(task: LocalizationTaskRow, jobs: list) -> list[dict[str, str]]:
    """Authoritative checkpoints when present; otherwise infer from executions."""
    from app.localization.checkpoints import (
        has_checkpoint_data,
        progress_steps,
        read_checkpoints,
    )

    if has_checkpoint_data(task.metadata_json):
        return progress_steps(read_checkpoints(task.metadata_json))

    kinds_done = {j.job_kind for j in jobs if j.status == "completed"}
    kinds_active = {j.job_kind for j in jobs if j.status in {"pending", "processing"}}
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
        {"id": "translate", "label": "Translating", "state": state("translate")},
        {"id": "validate", "label": "Validating", "state": "pending"},
        {"id": "write", "label": "Writing subtitle", "state": "pending"},
        {"id": "sync", "label": "Bazarr sync", "state": "pending"},
        {"id": "verify", "label": "Verification", "state": "pending"},
    ]
    if "translate" in kinds_done or "translate" in kinds_active:
        if steps[0]["state"] == "pending":
            steps[0]["state"] = "done"
        if steps[1]["state"] == "pending" and "extract" not in kinds_failed:
            steps[1]["state"] = "skipped"
    if "translate" in kinds_done:
        steps[2]["state"] = "done"
        steps[3]["state"] = "done"
        steps[4]["state"] = "done"
        if task.status == "verifying":
            steps[5]["state"] = "active"
            steps[6]["state"] = "active"
        elif task.status == "completed":
            steps[5]["state"] = "done"
            steps[6]["state"] = "done"
        elif any(j.reason_code == "bazarr_verify_failed" for j in jobs if j.job_kind == "translate"):
            steps[5]["state"] = "done"
            steps[6]["state"] = "failed"
    if task.status == "completed":
        for step in steps:
            if step["state"] == "pending":
                step["state"] = "skipped" if step["id"] == "extract" else "done"
    if task.status == "waiting_for_source":
        steps[0]["state"] = "active"
        if steps[1]["state"] == "pending":
            steps[1]["state"] = "skipped"
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
        provider = _bazarr_provider(db)
        release_session_connection(db)
        refs = await provider.search_media(q)
    except BazarrError as exc:
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
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[MediaItemOut]:
    return [_media_item_out(row) for row in MediaItemService(db).list_items(limit=limit)]


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

    # Prefer live Bazarr metadata when possible.
    ref: MediaRef | None = None
    try:
        provider = _bazarr_provider(db)
        ref = await provider.get_media(external_id)
    except (BazarrError, HTTPException):
        ref = None

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
    return _media_item_out(row)


@router.get("/media/{media_id}", response_model=MediaItemOut)
def get_media(media_id: int, db: Session = Depends(get_db)) -> MediaItemOut:
    row = MediaItemService(db).get(media_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return _media_item_out(row)


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

    return MediaLocalizationOut(media_id=media_id, capability="subtitles", languages=languages)


@router.get("/media/{media_id}/actions", response_model=list[JobActionOut])
def get_media_actions(media_id: int, db: Session = Depends(get_db)) -> list[JobActionOut]:
    media = MediaItemService(db).get(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return JobService(db).list_job_actions_for_media(media)


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
            reuse_active=False,
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
