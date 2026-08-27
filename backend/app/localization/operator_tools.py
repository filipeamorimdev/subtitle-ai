"""Whitelist of tools the operator chat may invoke.

Mutations go through LocalizationTaskService / TaskPlanner / JobService —
never by inventing extract/translate jobs. Confirm-gated tools return a
preview unless ``confirmed=True``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from app.ai.models import ToolSpec
from app.db import release_session_connection
from app.integrations.bazarr.client import BazarrError
from app.jobs.service import JobService
from app.jobs.worker import worker
from app.languages import LanguageNormalizationError, list_languages, normalize_language
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

CONFIRM_GATED = frozenset(
    {"transcribe_audio", "start_dub", "retry_task", "cancel_task"}
)

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class OperatorTool:
    spec: ToolSpec
    handler: ToolHandler
    requires_confirmation: bool = False


def _media_summary(row: Any) -> dict[str, Any]:
    return {
        "media_id": row.id,
        "title": row.title,
        "year": row.year,
        "media_type": row.media_type,
        "season": row.season,
        "episode": row.episode,
        "episode_title": row.episode_title,
        "path": row.path,
        "external_id": row.external_id,
        "provider_id": row.provider_id,
    }


def _task_summary(task: Any) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "media_item_id": task.media_item_id,
        "status": task.status,
        "substate": task.substate,
        "capability": task.capability,
        "target_language_code": task.target_language_code,
        "target_language_name": task.target_language_name,
        "origin": task.origin,
        "error_message": task.error_message,
    }


def _job_summary(job: Any) -> dict[str, Any]:
    return {
        "job_id": getattr(job, "id", None),
        "task_id": getattr(job, "task_id", None),
        "job_kind": getattr(job, "job_kind", None),
        "status": getattr(job, "status", None),
        "progress": getattr(job, "progress", None),
        "target_language": getattr(job, "target_language", None),
    }


def _bazarr_provider(db: Session) -> BazarrMediaProvider:
    from app.integrations.bazarr.client import BazarrClient

    settings = SettingsService(db)
    url, key = settings.get_bazarr_credentials()
    if not url:
        raise ValueError("Bazarr URL is not configured")
    return BazarrMediaProvider(BazarrClient(url, key))


def _confirmation_preview(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "needs_confirmation": True,
        "tool": name,
        "arguments": args,
        "preview": f"Confirm calling {name} with {json.dumps(args, default=str)}",
    }


async def search_media(db: Session, *, query: str, **_kwargs: Any) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"error": "invalid_arguments", "detail": "query is required"}
    try:
        provider = _bazarr_provider(db)
        release_session_connection(db)
        refs = await provider.search_media(q)
    except BazarrError as exc:
        return {"error": "bazarr_error", "detail": str(exc)}
    except ValueError as exc:
        return {"error": "not_configured", "detail": str(exc)}
    results = [
        {
            "provider_id": r.provider_id,
            "external_id": r.external_id,
            "media_type": r.media_type,
            "title": r.title,
            "year": r.year,
            "season": r.season,
            "episode": r.episode,
            "episode_title": r.episode_title,
            "path": r.path,
            "parent_external_id": r.parent_external_id,
            "bazarr_movie_id": r.bazarr_movie_id,
            "bazarr_series_id": r.bazarr_series_id,
            "bazarr_episode_id": r.bazarr_episode_id,
        }
        for r in refs
    ]
    return {
        "query": q,
        "count": len(results),
        "results": results,
        "ambiguous": len(results) > 1,
        "hint": (
            "Multiple matches — ask the user which title, then call ensure_media "
            "with that hit's fields before create_localization_task."
            if len(results) > 1
            else None
        ),
    }


async def ensure_media(
    db: Session,
    *,
    external_id: str | None = None,
    provider_id: str = BAZARR_PROVIDER_ID,
    media_type: str | None = None,
    title: str | None = None,
    year: int | None = None,
    path: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    episode_title: str | None = None,
    parent_external_id: str | None = None,
    bazarr_movie_id: int | None = None,
    bazarr_series_id: int | None = None,
    bazarr_episode_id: int | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    media_svc = MediaItemService(db)
    resolved_external = external_id
    if not resolved_external:
        if bazarr_movie_id is not None:
            resolved_external = movie_external_id(bazarr_movie_id)
        elif bazarr_episode_id is not None:
            resolved_external = episode_external_id(bazarr_episode_id)
        else:
            return {
                "error": "invalid_arguments",
                "detail": "external_id or Bazarr IDs required",
            }

    ref: MediaRef | None = None
    try:
        provider = _bazarr_provider(db)
        release_session_connection(db)
        ref = await provider.get_media(resolved_external)
    except (BazarrError, ValueError):
        ref = None

    if ref is None:
        if not title or not media_type:
            return {"error": "not_found", "detail": "Media not found"}
        ref = MediaRef(
            provider_id=provider_id or BAZARR_PROVIDER_ID,
            external_id=resolved_external,
            media_type=media_type,  # type: ignore[arg-type]
            title=title,
            year=year,
            season=season,
            episode=episode,
            episode_title=episode_title,
            path=path,
            parent_external_id=parent_external_id,
            bazarr_movie_id=bazarr_movie_id,
            bazarr_series_id=bazarr_series_id,
            bazarr_episode_id=bazarr_episode_id,
        )
    row = media_svc.upsert_from_ref(ref)
    return {"ok": True, **_media_summary(row)}


async def normalize_language_tool(
    db: Session, *, language: str, **_kwargs: Any
) -> dict[str, Any]:
    raw = (language or "").strip()
    if not raw:
        return {"error": "invalid_arguments", "detail": "language is required"}
    try:
        lang = normalize_language(raw)
    except LanguageNormalizationError as exc:
        return {"error": "invalid_language", "detail": str(exc)}
    return {
        "code": lang.code,
        "display_name": lang.display_name,
        "region": lang.region,
        "flag": lang.flag,
    }


async def get_media_localization(
    db: Session, *, media_id: int, **_kwargs: Any
) -> dict[str, Any]:
    media_svc = MediaItemService(db)
    row = media_svc.get(int(media_id))
    if row is None:
        return {"error": "not_found", "detail": "Media not found"}
    ref = media_svc.to_ref(row)

    languages: list[dict[str, Any]] = []
    try:
        provider = _bazarr_provider(db)
        release_session_connection(db)
        state = await provider.get_localization_state(ref)
        for item in state.languages:
            languages.append(
                {
                    "language_code": item.language_code,
                    "language_name": item.language_name,
                    "available": item.available,
                }
            )
    except (BazarrError, ValueError):
        from app.languages import list_featured_languages

        for lang in list_featured_languages():
            languages.append(
                {
                    "language_code": lang.code,
                    "language_name": lang.display_name,
                    "available": False,
                }
            )

    task_svc = LocalizationTaskService(db)
    for lang in languages:
        overlay = task_svc.latest_task_for_language(row.id, lang["language_code"])
        if overlay is None:
            continue
        lang["task_status"] = overlay.status
        lang["task_id"] = overlay.id
        lang["task_substate"] = overlay.substate

    seen_codes = {lang["language_code"] for lang in languages}
    for task in task_svc.list_tasks(media_item_id=row.id, capability="subtitles", limit=200):
        if any(languages_compatible(task.target_language_code, code) for code in seen_codes):
            continue
        overlay = task_svc.latest_task_for_language(row.id, task.target_language_code)
        if overlay is None or overlay.id != task.id:
            continue
        languages.append(
            {
                "language_code": task.target_language_code,
                "language_name": task.target_language_name,
                "available": task.status == "completed",
                "task_status": overlay.status,
                "task_id": overlay.id,
                "task_substate": overlay.substate,
            }
        )
        seen_codes.add(task.target_language_code)

    jobs = JobService(db)
    gate = await jobs.transcribe_gate_for_media(row)
    public = SettingsService(db).get_public()
    dub_gate = await jobs.dub_gate_for_media(
        row, target_language=public.target_language.code
    )
    return {
        "media": _media_summary(row),
        "languages": languages,
        "can_transcribe": gate.can_transcribe,
        "transcribe_reason": gate.reason,
        "can_dub": dub_gate.can_dub,
        "dub_reason": dub_gate.reason,
    }


async def create_localization_task(
    db: Session,
    *,
    media_id: int,
    target_language: str,
    capability: str = "subtitles",
    **_kwargs: Any,
) -> dict[str, Any]:
    media = MediaItemService(db).get(int(media_id))
    if media is None:
        return {"error": "not_found", "detail": "Media not found"}
    cap = (capability or "subtitles").strip().lower()
    try:
        task, _created = LocalizationTaskService(db).create_manual_task(
            media_item=media,
            target_language=target_language,
            capability=cap,
            requested_by="operator_chat",
        )
    except UnsupportedCapabilityError as exc:
        return {"error": "unsupported_capability", "detail": str(exc)}
    except LanguageNormalizationError as exc:
        return {"error": "invalid_language", "detail": str(exc)}
    except ActiveTaskExistsError as exc:
        return {
            "error": "active_task_exists",
            "task_id": exc.task_id,
            "detail": str(exc),
        }

    await TaskPlanner(db).plan(task.id)
    task = LocalizationTaskService(db).get(task.id)
    assert task is not None
    return {
        "ok": True,
        "media": _media_summary(media),
        **_task_summary(task),
    }


async def transcribe_audio(
    db: Session,
    *,
    media_id: int,
    target_language: str | None = None,
    confirmed: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    args = {"media_id": int(media_id)}
    if target_language:
        args["target_language"] = target_language
    if not confirmed:
        return _confirmation_preview("transcribe_audio", args)

    media = MediaItemService(db).get(int(media_id))
    if media is None:
        return {"error": "not_found", "detail": "Media not found"}
    try:
        job = await JobService(db).start_manual_transcribe(
            media, target_language=target_language
        )
    except ValueError as exc:
        return {"error": "rejected", "detail": str(exc)}
    return {"ok": True, "media": _media_summary(media), **_job_summary(job)}


async def start_dub(
    db: Session,
    *,
    media_id: int,
    target_language: str | None = None,
    replace_existing: bool = False,
    confirmed: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "media_id": int(media_id),
        "replace_existing": bool(replace_existing),
    }
    if target_language:
        args["target_language"] = target_language
    if not confirmed:
        return _confirmation_preview("start_dub", args)

    media = MediaItemService(db).get(int(media_id))
    if media is None:
        return {"error": "not_found", "detail": "Media not found"}
    try:
        job = await JobService(db).start_manual_dub(
            media,
            target_language=target_language,
            replace_existing=replace_existing,
        )
    except ValueError as exc:
        return {"error": "rejected", "detail": str(exc)}
    return {"ok": True, "media": _media_summary(media), **_job_summary(job)}


async def list_tasks(
    db: Session,
    *,
    status: str | None = None,
    media_item_id: int | None = None,
    active_only: bool = False,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    rows = LocalizationTaskService(db).list_tasks(
        status=status,
        media_item_id=int(media_item_id) if media_item_id is not None else None,
        active_only=bool(active_only),
        limit=max(1, min(int(limit or 20), 100)),
    )
    return {"count": len(rows), "tasks": [_task_summary(t) for t in rows]}


async def get_task(db: Session, *, task_id: int, **_kwargs: Any) -> dict[str, Any]:
    task = LocalizationTaskService(db).get(int(task_id))
    if task is None:
        return {"error": "not_found", "detail": "Task not found"}
    jobs = LocalizationTaskService(db).jobs_for_task(task.id)
    media = MediaItemService(db).get(task.media_item_id)
    return {
        **_task_summary(task),
        "media": _media_summary(media) if media else None,
        "jobs": [_job_summary(j) for j in jobs],
    }


async def retry_task(
    db: Session,
    *,
    task_id: int,
    confirmed: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    args = {"task_id": int(task_id)}
    if not confirmed:
        return _confirmation_preview("retry_task", args)
    try:
        task = LocalizationTaskService(db).prepare_retry(int(task_id))
    except ValueError as exc:
        return {"error": "rejected", "detail": str(exc)}
    await TaskPlanner(db).plan(task.id)
    task = LocalizationTaskService(db).get(task.id)
    assert task is not None
    return {"ok": True, **_task_summary(task)}


async def cancel_task(
    db: Session,
    *,
    task_id: int,
    confirmed: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    args = {"task_id": int(task_id)}
    if not confirmed:
        return _confirmation_preview("cancel_task", args)
    try:
        task = LocalizationTaskService(db).cancel(int(task_id))
    except ValueError as exc:
        return {"error": "rejected", "detail": str(exc)}
    for job in LocalizationTaskService(db).jobs_for_task(task.id):
        if job.status == "cancelled":
            worker.cancel_job(job.id)
    return {"ok": True, **_task_summary(task)}


def _build_registry() -> dict[str, OperatorTool]:
    return {
        "search_media": OperatorTool(
            spec=ToolSpec(
                name="search_media",
                description=(
                    "Search the Bazarr library for movies or episodes by title. "
                    "Call this before ensure_media / create_localization_task."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Title or search text",
                        }
                    },
                    "required": ["query"],
                },
            ),
            handler=search_media,
        ),
        "ensure_media": OperatorTool(
            spec=ToolSpec(
                name="ensure_media",
                description=(
                    "Persist a media identity from a search hit. Returns media_id "
                    "for create_localization_task."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "external_id": {"type": "string"},
                        "provider_id": {"type": "string"},
                        "media_type": {
                            "type": "string",
                            "enum": ["movie", "series", "episode"],
                        },
                        "title": {"type": "string"},
                        "year": {"type": "integer"},
                        "path": {"type": "string"},
                        "season": {"type": "integer"},
                        "episode": {"type": "integer"},
                        "episode_title": {"type": "string"},
                        "parent_external_id": {"type": "string"},
                        "bazarr_movie_id": {"type": "integer"},
                        "bazarr_series_id": {"type": "integer"},
                        "bazarr_episode_id": {"type": "integer"},
                    },
                },
            ),
            handler=ensure_media,
        ),
        "normalize_language": OperatorTool(
            spec=ToolSpec(
                name="normalize_language",
                description=(
                    "Normalize a language name or code to a catalog code "
                    "(e.g. Portuguese of Portugal → pt-PT)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "language": {
                            "type": "string",
                            "description": "Language name or code",
                        }
                    },
                    "required": ["language"],
                },
            ),
            handler=normalize_language_tool,
        ),
        "get_media_localization": OperatorTool(
            spec=ToolSpec(
                name="get_media_localization",
                description="Language availability and task status for one media item.",
                parameters={
                    "type": "object",
                    "properties": {
                        "media_id": {"type": "integer"},
                    },
                    "required": ["media_id"],
                },
            ),
            handler=get_media_localization,
        ),
        "create_localization_task": OperatorTool(
            spec=ToolSpec(
                name="create_localization_task",
                description=(
                    "Create a localization goal for media_id + language. "
                    "TaskPlanner chooses extract/request/transcribe/translate. "
                    "Requires a numeric media_id from ensure_media — never a title string."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "media_id": {"type": "integer"},
                        "target_language": {
                            "type": "string",
                            "description": "Language name or code (prefer catalog code)",
                        },
                        "capability": {
                            "type": "string",
                            "enum": ["subtitles", "audio"],
                            "default": "subtitles",
                        },
                    },
                    "required": ["media_id", "target_language"],
                },
            ),
            handler=create_localization_task,
        ),
        "transcribe_audio": OperatorTool(
            spec=ToolSpec(
                name="transcribe_audio",
                description=(
                    "Start Whisper ASR for a media item. Requires user confirmation."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "media_id": {"type": "integer"},
                        "target_language": {"type": "string"},
                        "confirmed": {"type": "boolean"},
                    },
                    "required": ["media_id"],
                },
            ),
            handler=transcribe_audio,
            requires_confirmation=True,
        ),
        "start_dub": OperatorTool(
            spec=ToolSpec(
                name="start_dub",
                description=(
                    "Start a background-preserved TTS dub when a target SRT already exists. "
                    "Requires user confirmation."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "media_id": {"type": "integer"},
                        "target_language": {"type": "string"},
                        "replace_existing": {"type": "boolean"},
                        "confirmed": {"type": "boolean"},
                    },
                    "required": ["media_id"],
                },
            ),
            handler=start_dub,
            requires_confirmation=True,
        ),
        "list_tasks": OperatorTool(
            spec=ToolSpec(
                name="list_tasks",
                description="List localization tasks, optionally filtered.",
                parameters={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "media_item_id": {"type": "integer"},
                        "active_only": {"type": "boolean"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            handler=list_tasks,
        ),
        "get_task": OperatorTool(
            spec=ToolSpec(
                name="get_task",
                description="Get one localization task and its jobs.",
                parameters={
                    "type": "object",
                    "properties": {"task_id": {"type": "integer"}},
                    "required": ["task_id"],
                },
            ),
            handler=get_task,
        ),
        "retry_task": OperatorTool(
            spec=ToolSpec(
                name="retry_task",
                description="Retry a failed localization task. Requires confirmation.",
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer"},
                        "confirmed": {"type": "boolean"},
                    },
                    "required": ["task_id"],
                },
            ),
            handler=retry_task,
            requires_confirmation=True,
        ),
        "cancel_task": OperatorTool(
            spec=ToolSpec(
                name="cancel_task",
                description="Cancel an active localization task. Requires confirmation.",
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer"},
                        "confirmed": {"type": "boolean"},
                    },
                    "required": ["task_id"],
                },
            ),
            handler=cancel_task,
            requires_confirmation=True,
        ),
    }


OPERATOR_TOOLS: dict[str, OperatorTool] = _build_registry()


def tool_specs() -> list[ToolSpec]:
    return [t.spec for t in OPERATOR_TOOLS.values()]


def tool_schemas_openai() -> list[dict[str, Any]]:
    return [t.spec.to_openai_dict() for t in OPERATOR_TOOLS.values()]


def _filter_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return dict(args)
    allowed = set(props.keys())
    return {k: v for k, v in args.items() if k in allowed}


def _missing_required(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(required, list):
        return []
    return [str(k) for k in required if k not in args or args[k] is None]


async def call_tool(
    db: Session,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Execute a whitelisted tool. Unknown names return an error payload."""
    tool = OPERATOR_TOOLS.get(name)
    if tool is None:
        return {
            "error": "unknown_tool",
            "detail": f"Tool {name!r} is not available",
            "available": sorted(OPERATOR_TOOLS.keys()),
        }
    raw = dict(arguments or {})
    if confirmed:
        raw["confirmed"] = True
    filtered = _filter_args(tool.spec.parameters, raw)
    missing = _missing_required(tool.spec.parameters, filtered)
    if missing:
        return {
            "error": "invalid_arguments",
            "detail": f"Missing required fields: {', '.join(missing)}",
        }
    try:
        return await tool.handler(db, **filtered)
    except TypeError as exc:
        return {"error": "invalid_arguments", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": "tool_failed", "detail": str(exc)[:500]}


def list_available_languages_hint() -> list[dict[str, str]]:
    """Small catalog sample for system prompts (not a tool)."""
    return [
        {"code": lang.code, "display_name": lang.display_name}
        for lang in list_languages()[:40]
    ]
