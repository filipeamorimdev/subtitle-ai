"""API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import __version__
from app.api.schemas import (
    AutomationScanResult,
    AutomationStatusOut,
    BatchJobsOut,
    CandidateOut,
    ClearDataResult,
    ClearJobsRequest,
    ConnectionTestResult,
    ExtractCreate,
    HealthOut,
    JobActionOut,
    JobCreate,
    JobLogOut,
    JobOut,
    JobRequestLogOut,
    JobUsageExchangeOut,
    JobUsageOut,
    OpenRouterModelOut,
    OpenRouterModelsOut,
    RequestSubtitleCreate,
    SettingsOut,
    SettingsUpdate,
    StatsOut,
)
from app.db import get_db
from app.integrations.bazarr.client import BazarrClient, BazarrError
from app.jobs.scanner import scanner
from app.jobs.service import JobService
from app.jobs.worker import worker
from app.services.candidates import CandidateService
from app.services.settings import SettingsService
from app.subtitles.embedded import EmbeddedError
from app.translation.openrouter.client import OpenRouterClient, OpenRouterError

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthOut)
async def health(
    request: Request,
    live: bool = False,
    db: Session = Depends(get_db),
) -> HealthOut:
    bazarr = "unknown"
    openrouter = "unknown"
    try:
        settings = SettingsService(db).get_public()
        bazarr = "configured" if settings.bazarr_url else "not_configured"
        openrouter = "configured" if settings.openrouter_api_key_configured else "not_configured"
    except Exception:  # noqa: BLE001
        bazarr = "unknown"
        openrouter = "unknown"
    if live:
        from app.core.health import probe_bazarr, probe_openrouter

        bazarr = await probe_bazarr(db)
        openrouter = await probe_openrouter(db)
    planner_error = getattr(request.app.state, "planner_error", None)
    status = "degraded" if planner_error else "ok"
    return HealthOut(
        status=status,
        version=__version__,
        database="healthy",
        bazarr=bazarr,
        openrouter=openrouter,
        planner_error=planner_error,
    )


@router.get("/events")
async def stream_events():
    import asyncio

    from app.core.events import subscribe, unsubscribe

    queue = await subscribe()

    async def generate():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/settings", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)) -> SettingsOut:
    return SettingsService(db).get_public()


@router.put("/settings", response_model=SettingsOut)
def put_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> SettingsOut:
    return SettingsService(db).update(payload)


@router.get("/settings/export")
def export_settings(db: Session = Depends(get_db)) -> dict:
    public = SettingsService(db).get_public()
    data = public.model_dump()
    for key in list(data):
        if "key" in key and "configured" not in key:
            data[key] = None
    return {"settings": data, "secrets_omitted": True}


@router.post("/settings/import", response_model=SettingsOut)
def import_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> SettingsOut:
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


@router.get("/settings/openrouter/models", response_model=OpenRouterModelsOut)
async def list_openrouter_models(db: Session = Depends(get_db)) -> OpenRouterModelsOut:
    from app.services.model_catalog import ModelCatalogService, check_compatibility

    service = SettingsService(db)
    public = service.get_public()
    catalog = ModelCatalogService(db)
    try:
        snapshot = await catalog.get_models()
    except OpenRouterError as exc:
        cached = catalog.get_cached()
        if cached is None:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        snapshot = cached
    return OpenRouterModelsOut(
        models=[
            OpenRouterModelOut(
                id=m.id,
                name=m.name,
                prompt_price_per_million=m.prompt_price_per_million,
                completion_price_per_million=m.completion_price_per_million,
                context_length=m.context_length,
                pricing_tier=m.pricing_tier,
                description=m.description,
                compatible=check_compatibility(m, batch_size=public.batch_size)[0],
                compatibility_reason=check_compatibility(m, batch_size=public.batch_size)[1],
                stale=snapshot.stale,
                unavailable=False,
                input_modalities=m.input_modalities,
                output_modalities=m.output_modalities,
            )
            for m in snapshot.models
        ]
    )


@router.post("/settings/clear/jobs", response_model=ClearDataResult)
def clear_jobs(
    payload: ClearJobsRequest = ClearJobsRequest(),
    db: Session = Depends(get_db),
) -> ClearDataResult:
    return JobService(db).clear_jobs(job_kind=payload.job_kind, status=payload.status)


@router.post("/settings/clear/usage", response_model=ClearDataResult)
def clear_usage_stats(db: Session = Depends(get_db)) -> ClearDataResult:
    return JobService(db).clear_usage_stats()


@router.get("/candidates", response_model=list[CandidateOut])
async def list_candidates(db: Session = Depends(get_db)) -> list[CandidateOut]:
    try:
        return await CandidateService(db).list_candidates(force_refresh=False)
    except BazarrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates/refresh", response_model=list[CandidateOut])
async def refresh_candidates(db: Session = Depends(get_db)) -> list[CandidateOut]:
    try:
        return await CandidateService(db).list_candidates(force_refresh=True)
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


@router.post("/candidates/batch/request-subtitle", response_model=BatchJobsOut)
async def batch_request_subtitle(db: Session = Depends(get_db)) -> BatchJobsOut:
    try:
        return await JobService(db).batch_request_missing_source()
    except BazarrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates/batch/extract", response_model=BatchJobsOut)
async def batch_extract(db: Session = Depends(get_db)) -> BatchJobsOut:
    try:
        return await JobService(db).batch_extract()
    except BazarrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates/batch/translate", response_model=BatchJobsOut)
async def batch_translate(db: Session = Depends(get_db)) -> BatchJobsOut:
    try:
        return await JobService(db).batch_translate()
    except OpenRouterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    return JobService(db).list_jobs(status=status, limit=limit)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobOut:
    job = JobService(db).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/actions", response_model=list[JobActionOut])
def list_job_actions(job_id: int, db: Session = Depends(get_db)) -> list[JobActionOut]:
    actions = JobService(db).list_job_actions(job_id)
    if actions is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return actions


@router.get("/jobs/{job_id}/log", response_model=JobLogOut)
def get_job_log(job_id: int, db: Session = Depends(get_db)) -> JobLogOut:
    log = JobService(db).get_job_log(job_id)
    if not log:
        raise HTTPException(status_code=404, detail="Job not found")
    return log


@router.get("/jobs/{job_id}/requests", response_model=list[JobUsageExchangeOut])
def list_job_requests(job_id: int, db: Session = Depends(get_db)) -> list[JobUsageExchangeOut]:
    requests = JobService(db).list_job_requests(job_id)
    if requests is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return requests


@router.get("/jobs/{job_id}/requests/{index}", response_model=JobRequestLogOut)
def get_job_request_log(job_id: int, index: int, db: Session = Depends(get_db)) -> JobRequestLogOut:
    log = JobService(db).get_job_request_log(job_id, index)
    if not log:
        raise HTTPException(status_code=404, detail="Request log not found")
    return log


@router.get("/jobs/{job_id}/usage", response_model=JobUsageOut)
async def get_job_usage(job_id: int, db: Session = Depends(get_db)) -> JobUsageOut:
    usage = await JobService(db).get_job_usage(job_id)
    if not usage:
        raise HTTPException(status_code=404, detail="Job not found")
    return usage


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
        result = JobService(db).cancel_job(job_id)
        worker.cancel_job(job_id)
        return result
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


@router.get("/automation/status", response_model=AutomationStatusOut)
def automation_status() -> AutomationStatusOut:
    return scanner.status()


@router.post("/automation/run", response_model=AutomationScanResult)
async def automation_run() -> AutomationScanResult:
    return await scanner.scan_once()
