"""Operator chat agent loop — OpenRouter tool calls over localization tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import (
    CAPABILITY_FUNCTION_CALLING,
    AIResponse,
    Message,
    ToolCall,
)
from app.ai.providers.base import AIProvider
from app.ai.providers.openrouter import PROVIDER_ID as OPENROUTER_PROVIDER_ID
from app.ai.providers.registry import get_provider_registry
from app.core.logging import get_logger
from app.db.models import OperatorChatMessageRow, OperatorChatSessionRow
from app.localization.operator_tools import call_tool, tool_specs
from app.services.ai_usage import RecordingAIProvider
from app.services.model_catalog import ModelCatalogService
from app.services.model_preferences import ModelPreferenceService
from app.services.settings import SettingsService

logger = get_logger("operator")

MAX_TURNS = 8

SYSTEM_PROMPT = """You are Subtitle AI's library operator assistant.
You only mutate the library by calling tools. Assistant prose never creates jobs.

Rules:
- Search media before acting. If several titles match, ask which one — do not pick silently.
- create_localization_task requires a numeric media_id from ensure_media (never a free-text title).
- Prefer normalize_language before create_localization_task when the user names a language.
- Do not choose extract vs request vs transcribe vs translate; create_localization_task lets TaskPlanner decide.
- transcribe_audio, start_dub, retry_task, and cancel_task require confirmation — call them without confirmed=true first so the UI can ask; only pass confirmed=true after the user confirms.
- Be concise. After a successful mutation, summarize what started (task id / status) and stop.
"""


@dataclass
class ConfirmedTool:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class OperatorTurnResult:
    assistant_text: str
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    pending_confirmation: dict[str, Any] | None = None
    media_links: list[dict[str, Any]] = field(default_factory=list)
    model_id: str | None = None


class OperatorModelUnavailable(Exception):
    """No chat-capable model configured."""


def resolve_operator_model_id(db: Session) -> str:
    """Configured chat model, else first pool model advertising function_calling."""
    public = SettingsService(db).get_public()
    configured = (public.operator_model_id or "").strip()
    catalog = ModelCatalogService(db)

    def supports_tools(provider_id: str, model_id: str) -> bool:
        model = catalog.get_model(provider_id, model_id)
        if model is None:
            return False
        return CAPABILITY_FUNCTION_CALLING in (model.capabilities or set())

    if configured:
        if supports_tools(OPENROUTER_PROVIDER_ID, configured):
            return configured
        # User picked a model — still try it (catalog may be stale).
        return configured

    prefs = ModelPreferenceService(db).list_preferences(enabled_only=True)
    paid = [p for p in prefs if p.tier == "paid"]
    free = [p for p in prefs if p.tier == "free"]
    for pool in (paid, free):
        for pref in sorted(pool, key=lambda p: p.priority):
            if supports_tools(pref.provider_id, pref.model_id):
                return pref.model_id
    # Last resort: any enabled preference (may fail at call time).
    for pool in (paid, free):
        ordered = sorted(pool, key=lambda p: p.priority)
        if ordered:
            return ordered[0].model_id
    raise OperatorModelUnavailable(
        "No chat model available. Set a Chat model under Settings → Models "
        "(needs tool/function calling support)."
    )


def _message_to_content_json(msg: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": msg.role}
    if msg.content is not None:
        payload["content"] = msg.content
    if msg.tool_call_id:
        payload["tool_call_id"] = msg.tool_call_id
    if msg.name:
        payload["name"] = msg.name
    if msg.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
                "arguments_raw": tc.arguments_raw,
            }
            for tc in msg.tool_calls
        ]
    return payload


def _content_json_to_message(data: dict[str, Any]) -> Message:
    tool_calls = None
    raw_calls = data.get("tool_calls")
    if isinstance(raw_calls, list) and raw_calls:
        tool_calls = []
        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            tool_calls.append(
                ToolCall(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or ""),
                    arguments=dict(item.get("arguments") or {}),
                    arguments_raw=item.get("arguments_raw")
                    if isinstance(item.get("arguments_raw"), str)
                    else None,
                )
            )
    content = data.get("content")
    return Message(
        role=str(data.get("role") or "user"),
        content=content if isinstance(content, str) or content is None else str(content),
        tool_calls=tool_calls,
        tool_call_id=str(data["tool_call_id"]) if data.get("tool_call_id") else None,
        name=str(data["name"]) if data.get("name") else None,
    )


def create_session(db: Session) -> OperatorChatSessionRow:
    row = OperatorChatSessionRow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session(db: Session, session_id: int) -> OperatorChatSessionRow | None:
    return db.get(OperatorChatSessionRow, session_id)


def list_messages(db: Session, session_id: int) -> list[OperatorChatMessageRow]:
    return list(
        db.scalars(
            select(OperatorChatMessageRow)
            .where(OperatorChatMessageRow.session_id == session_id)
            .order_by(OperatorChatMessageRow.id.asc())
        ).all()
    )


def _append_message(db: Session, session_id: int, msg: Message) -> OperatorChatMessageRow:
    row = OperatorChatMessageRow(
        session_id=session_id,
        role=msg.role,
        content_json=_message_to_content_json(msg),
    )
    db.add(row)
    session = db.get(OperatorChatSessionRow, session_id)
    if session is not None:
        from datetime import datetime, timezone

        session.updated_at = datetime.now(timezone.utc)
        db.add(session)
    db.commit()
    db.refresh(row)
    return row


def _build_provider(db: Session) -> AIProvider:
    registry = get_provider_registry()
    provider = registry.get(OPENROUTER_PROVIDER_ID)
    if provider is None or not provider.is_configured():
        raise OperatorModelUnavailable("OpenRouter is not configured.")
    from app.services.ai_usage import AiUsageService

    return RecordingAIProvider(
        provider,
        AiUsageService(db),
        job_id=None,
        trigger_type="manual",
        default_operation="operator_chat",
    )


def _collect_media_links(payload: dict[str, Any], sink: list[dict[str, Any]]) -> None:
    media_id = payload.get("media_id") or (payload.get("media") or {}).get("media_id")
    if media_id is None and payload.get("media_item_id") is not None:
        media_id = payload.get("media_item_id")
    title = None
    media = payload.get("media")
    if isinstance(media, dict):
        title = media.get("title")
        media_id = media_id or media.get("media_id")
    if media_id is None:
        return
    link = {"media_id": int(media_id), "title": title, "task_id": payload.get("task_id")}
    if link not in sink:
        sink.append(link)


class OperatorLoop:
    def __init__(self, db: Session, *, provider: AIProvider | None = None) -> None:
        self.db = db
        self._provider = provider

    async def handle_user_message(
        self,
        session_id: int,
        content: str,
        *,
        confirmed_tool: ConfirmedTool | None = None,
    ) -> OperatorTurnResult:
        session = get_session(self.db, session_id)
        if session is None:
            raise ValueError("Session not found")

        text = (content or "").strip()
        if not text and confirmed_tool is None:
            raise ValueError("Message content is required")

        if text:
            _append_message(self.db, session_id, Message(role="user", content=text))

        if confirmed_tool is not None:
            args = dict(confirmed_tool.arguments or {})
            args["confirmed"] = True
            result = await call_tool(
                self.db,
                confirmed_tool.name,
                args,
                confirmed=True,
            )
            tool_msg = Message(
                role="tool",
                content=json.dumps(result, default=str),
                tool_call_id="confirmed",
                name=confirmed_tool.name,
            )
            _append_message(self.db, session_id, tool_msg)
            # Nudge the model to summarize after confirmation execution.
            _append_message(
                self.db,
                session_id,
                Message(
                    role="user",
                    content=(
                        f"The user confirmed {confirmed_tool.name}. "
                        f"Tool result: {json.dumps(result, default=str)}. "
                        "Summarize briefly; do not re-call the same mutation."
                    ),
                ),
            )

        return await self._run_loop(session_id)

    async def _run_loop(self, session_id: int) -> OperatorTurnResult:
        model_id = resolve_operator_model_id(self.db)
        provider = self._provider or _build_provider(self.db)
        public = SettingsService(self.db).get_public()
        temperature = float(public.openrouter_temperature or 0)

        tool_events: list[dict[str, Any]] = []
        media_links: list[dict[str, Any]] = []
        pending: dict[str, Any] | None = None
        assistant_text = ""

        for _turn in range(MAX_TURNS):
            history = list_messages(self.db, session_id)
            messages: list[Message] = [Message(role="system", content=SYSTEM_PROMPT)]
            for row in history:
                if isinstance(row.content_json, dict):
                    messages.append(_content_json_to_message(row.content_json))

            response: AIResponse = await provider.chat_completion(
                model_id=model_id,
                messages=messages,
                temperature=temperature,
                tools=tool_specs(),
                tool_choice="auto",
            )

            if response.tool_calls:
                _append_message(
                    self.db,
                    session_id,
                    Message(
                        role="assistant",
                        content=response.content or None,
                        tool_calls=response.tool_calls,
                    ),
                )
                for tc in response.tool_calls:
                    result = await call_tool(self.db, tc.name, tc.arguments)
                    event = {
                        "tool": tc.name,
                        "arguments": tc.arguments,
                        "result": result,
                    }
                    tool_events.append(event)
                    _collect_media_links(result if isinstance(result, dict) else {}, media_links)
                    if isinstance(result, dict) and result.get("needs_confirmation"):
                        pending = result
                    _append_message(
                        self.db,
                        session_id,
                        Message(
                            role="tool",
                            content=json.dumps(result, default=str),
                            tool_call_id=tc.id or "tool",
                            name=tc.name,
                        ),
                    )
                if pending is not None:
                    # Stop so the UI can confirm before continuing.
                    assistant_text = (
                        response.content
                        or f"Confirm {pending.get('tool')} before continuing."
                    )
                    _append_message(
                        self.db,
                        session_id,
                        Message(role="assistant", content=assistant_text),
                    )
                    break
                continue

            assistant_text = (response.content or "").strip()
            if assistant_text:
                _append_message(
                    self.db,
                    session_id,
                    Message(role="assistant", content=assistant_text),
                )
            break
        else:
            if not assistant_text:
                assistant_text = "I hit the tool-call limit. Try a simpler request."
                _append_message(
                    self.db,
                    session_id,
                    Message(role="assistant", content=assistant_text),
                )

        return OperatorTurnResult(
            assistant_text=assistant_text,
            tool_events=tool_events,
            pending_confirmation=pending,
            media_links=media_links,
            model_id=model_id,
        )
