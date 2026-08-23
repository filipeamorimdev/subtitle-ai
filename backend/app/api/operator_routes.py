"""Operator chat HTTP API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.localization.operator import (
    ConfirmedTool,
    OperatorLoop,
    OperatorModelUnavailable,
    create_session,
    get_session,
    list_messages,
    resolve_operator_model_id,
)
from app.services.settings import SettingsService

router = APIRouter(prefix="/api/operator", tags=["operator"])


class OperatorSessionOut(BaseModel):
    id: int


class OperatorMessageOut(BaseModel):
    id: int
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class OperatorSessionDetailOut(BaseModel):
    id: int
    messages: list[OperatorMessageOut]
    operator_model_id: str | None = None
    operator_ready: bool = False


class ConfirmedToolIn(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class OperatorMessageIn(BaseModel):
    content: str = ""
    confirmed_tool: ConfirmedToolIn | None = None


class OperatorTurnOut(BaseModel):
    session_id: int
    assistant_text: str
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    pending_confirmation: dict[str, Any] | None = None
    media_links: list[dict[str, Any]] = Field(default_factory=list)
    model_id: str | None = None


def _message_out(row: Any) -> OperatorMessageOut:
    data = row.content_json if isinstance(row.content_json, dict) else {}
    return OperatorMessageOut(
        id=row.id,
        role=row.role,
        content=data.get("content") if isinstance(data.get("content"), str) else None,
        tool_calls=data.get("tool_calls") if isinstance(data.get("tool_calls"), list) else None,
        tool_call_id=data.get("tool_call_id"),
        name=data.get("name"),
    )


@router.post("/sessions", response_model=OperatorSessionOut)
def create_operator_session(db: Session = Depends(get_db)) -> OperatorSessionOut:
    row = create_session(db)
    return OperatorSessionOut(id=row.id)


@router.get("/sessions/{session_id}", response_model=OperatorSessionDetailOut)
def get_operator_session(session_id: int, db: Session = Depends(get_db)) -> OperatorSessionDetailOut:
    session = get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    public = SettingsService(db).get_public()
    model_id: str | None = None
    ready = False
    if public.openrouter_api_key_configured:
        try:
            model_id = resolve_operator_model_id(db)
            ready = True
        except OperatorModelUnavailable:
            ready = False
    return OperatorSessionDetailOut(
        id=session.id,
        messages=[_message_out(m) for m in list_messages(db, session.id)],
        operator_model_id=model_id or public.operator_model_id,
        operator_ready=ready,
    )


@router.get("/status")
def operator_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    public = SettingsService(db).get_public()
    if not public.openrouter_api_key_configured:
        return {
            "ready": False,
            "reason": "OpenRouter is not configured.",
            "operator_model_id": None,
        }
    try:
        model_id = resolve_operator_model_id(db)
    except OperatorModelUnavailable as exc:
        return {
            "ready": False,
            "reason": str(exc),
            "operator_model_id": public.operator_model_id,
        }
    return {"ready": True, "reason": None, "operator_model_id": model_id}


@router.post("/sessions/{session_id}/messages", response_model=OperatorTurnOut)
async def post_operator_message(
    session_id: int,
    payload: OperatorMessageIn,
    db: Session = Depends(get_db),
) -> OperatorTurnOut:
    session = get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    confirmed = None
    if payload.confirmed_tool is not None:
        confirmed = ConfirmedTool(
            name=payload.confirmed_tool.name,
            arguments=dict(payload.confirmed_tool.arguments or {}),
        )
    try:
        result = await OperatorLoop(db).handle_user_message(
            session_id,
            payload.content,
            confirmed_tool=confirmed,
        )
    except OperatorModelUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OperatorTurnOut(
        session_id=session_id,
        assistant_text=result.assistant_text,
        tool_events=result.tool_events,
        pending_confirmation=result.pending_confirmation,
        media_links=result.media_links,
        model_id=result.model_id,
    )
