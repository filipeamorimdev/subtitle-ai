"""API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import __version__
from app.api.schemas import (
    CandidateOut,
    ConnectionTestResult,
    ExtractCreate,
    HealthOut,
    JobCreate,
    JobLogOut,
    JobOut,
    RequestSubtitleCreate,
    SettingsOut,
    SettingsUpdate,
    StatsOut,
)
from app.db import get_db
from app.integrations.bazarr.client import BazarrClient, BazarrError
from app.jobs.service import JobService
from app.services.candidates import CandidateService
from app.services.settings import SettingsService
from app.subtitles.embedded import EmbeddedError
from app.translation.openrouter.client import OpenRouterClient, OpenRouterError

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    bazarr = "unknown"
    openrouter = "unknown"
    try:
        settings = SettingsService(db).get_public()
        bazarr = "configured" if settings.bazarr_url else "not_configured"
        openrouter = "configured" if settings.openrouter_api_key_configured else "not_configured"
    except Exception:  # noqa: BLE001
        bazarr = "unknown"
        openrouter = "unknown"
    return HealthOut(
        status="ok",
        version=__version__,
        database="healthy",
        bazarr=bazarr,
        openrouter=openrouter,
    )


@router.get("/settings", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)) -> SettingsOut:
    return SettingsService(db).get_public()


@router.put("/settings", response_model=SettingsOut)
def put_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> SettingsOut:
    return SettingsService(db).update(payload)


@router.post("/settings/test/bazarr", response_model=ConnectionTestResult)
async def test_bazarr(db: Session = Depends(get_db)) -> ConnectionTestResult:
    service = SettingsService(db)
    url, key = service.get_bazarr_credentials()
    if not url:
        return ConnectionTestResult(ok=False, message="Bazarr URL is not configured.")
    try:
        details = await BazarrClient(url, key).test_connection()
        return ConnectionTestResult(ok=True, message="Connected to Bazarr.", details=details)
    except BazarrError as exc:
        return ConnectionTestResult(ok=False, message=str(exc))


@router.post("/settings/test/openrouter", response_model=ConnectionTestResult)
async def test_openrouter(db: Session = Depends(get_db)) -> ConnectionTestResult:
    service = SettingsService(db)
    key, model = service.get_openrouter_credentials()
    if not key:
        return ConnectionTestResult(ok=False, message="OpenRouter API key is not configured.")
    try:
        details = await OpenRouterClient(key).test_connection(model)
        return ConnectionTestResult(
            ok=True,
            message="Connected to OpenRouter.",
            details=details,
        )
    except OpenRouterError as exc:
        return ConnectionTestResult(ok=False, message=str(exc))


@router.get("/candidates", response_model=list[CandidateOut])
async def list_candidates(db: Session = Depends(get_db)) -> list[CandidateOut]:
    try:
        return await CandidateService(db).list_candidates()
    except BazarrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates/refresh", response_model=list[CandidateOut])
async def refresh_candidates(db: Session = Depends(get_db)) -> list[CandidateOut]:
    try:
        return await CandidateService(db).list_candidates()
    except BazarrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates/extract", response_model=JobOut)
async def extract_candidate(payload: ExtractCreate, db: Session = Depends(get_db)) -> JobOut:
    try:
        return await JobService(db).create_extract_job(payload)
    except (ValueError, EmbeddedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates/request-subtitle", response_model=JobOut)
async def request_subtitle(
    payload: RequestSubtitleCreate,
    db: Session = Depends(get_db),
) -> JobOut:
    try:
        return await JobService(db).create_request_subtitle_job(
            payload.candidate_key,
            language=payload.language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BazarrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db)) -> list[JobOut]:
    return JobService(db).list_jobs()


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobOut:
    job = JobService(db).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/log", response_model=JobLogOut)
def get_job_log(job_id: int, db: Session = Depends(get_db)) -> JobLogOut:
    log = JobService(db).get_job_log(job_id)
    if not log:
        raise HTTPException(status_code=404, detail="Job not found")
    return log


@router.post("/jobs", response_model=JobOut)
async def create_job(payload: JobCreate, db: Session = Depends(get_db)) -> JobOut:
    try:
        return await JobService(db).create_job(payload)
    except (ValueError, OpenRouterError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: int, db: Session = Depends(get_db)) -> JobOut:
    try:
        return await JobService(db).retry_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, db: Session = Depends(get_db)) -> JobOut:
    try:
        return JobService(db).cancel_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry-bazarr-sync", response_model=JobOut)
async def retry_bazarr_sync(job_id: int, db: Session = Depends(get_db)) -> JobOut:
    try:
        return await JobService(db).retry_bazarr_sync(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db)) -> StatsOut:
    return JobService(db).stats()
