"""Glossary memory: scopes, terms, extraction, and resolution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.ai.errors import AIProviderError
from app.core.logging import get_logger
from app.db.models import GlossaryScopeRow, GlossaryTermRow
from app.services.glossary_universes import UniverseDef, match_universe, universe_keys
from app.subtitles.models import SubtitleDocument
from app.translation.openrouter.client import ChatResult, batch_base_model

# Alias for transitional catch blocks.
OpenRouterError = AIProviderError


async def _provider_chat(
    client: Any, *, model: str, messages: list[dict[str, str]], **kwargs: Any
) -> Any:
    """Call chat_completion with model_id= (AIProvider) or model= (legacy client)."""
    try:
        return await client.chat_completion(model_id=model, messages=messages, **kwargs)
    except TypeError:
        return await client.chat_completion(model=model, messages=messages, **kwargs)


logger = get_logger("glossary")

TERM_TYPES = {"character", "place", "organization", "title", "catchphrase", "other"}
POLICIES = {"keep", "localize", "transliterate"}
STATUSES = {"active", "suggested", "rejected"}
MAX_EXTRACT_TERMS = 80
MAX_SAMPLE_CHARS = 12_000


@dataclass
class GlossaryTermView:
    source: str
    target: str
    term_type: str
    policy: str
    status: str = "active"
    locked: bool = False
    scope_id: int | None = None
    scope_kind: str | None = None
    scope_name: str | None = None
    term_id: int | None = None


@dataclass
class ResolvedGlossary:
    leaf_scope: GlossaryScopeRow
    scopes: list[GlossaryScopeRow]
    terms: list[GlossaryTermView]
    usage_input_tokens: int = 0
    usage_output_tokens: int = 0
    usage_total_tokens: int = 0
    suggested_new: int = 0
    universe_key: str | None = None


def normalize_source(source: str) -> str:
    return re.sub(r"\s+", " ", source.strip()).casefold()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:200] or "untitled"


def sample_document_text(document: SubtitleDocument, *, max_chars: int = MAX_SAMPLE_CHARS) -> str:
    blocks = document.blocks
    if not blocks:
        return ""
    chunks: list[str] = []
    # Prefer opening blocks (credits / names) plus a middle slice.
    head = blocks[:40]
    mid_start = max(0, len(blocks) // 2 - 15)
    mid = blocks[mid_start : mid_start + 30]
    seen: set[int] = set()
    for block in [*head, *mid]:
        if block.index in seen:
            continue
        seen.add(block.index)
        chunks.append(block.text.strip())
    text = "\n".join(c for c in chunks if c)
    if len(text) > max_chars:
        return text[:max_chars]
    return text


EXTRACT_SYSTEM_PROMPT = """You extract audiovisual glossary terms for subtitle translation consistency.

Return ONLY valid JSON with this shape:
{"terms":[{"source":"...","target":"...","type":"character|place|organization|title|catchphrase|other","policy":"keep|localize|transliterate"}]}

Rules:
- Focus on character names, places, organizations, titles, and recurring catchphrases.
- Provide the preferred target-language form for each source term.
- Use policy "keep" when the form should stay unchanged; "localize" for established local forms; "transliterate" when needed.
- Prefer multi-word proper names over generic words.
- Do not invent terms absent from the dialogue sample or title.
- Cap at 60 terms.
- No markdown, no commentary.
"""

UNIVERSE_CLASSIFY_SYSTEM = """Classify media into a franchise universe for subtitle glossary sharing.

Return ONLY valid JSON:
{"universe":"marvel|dc|star-wars|harry-potter|lord-of-the-rings|star-trek|none|other","other_key":null,"other_name":null}

Use "none" when there is no shared franchise universe.
Use "other" only for a clear shared franchise not in the list; then set other_key (slug) and other_name.
No markdown.
"""


def build_extract_user_message(
    *,
    media_title: str | None,
    target_language_code: str,
    target_language_name: str,
    sample_text: str,
) -> str:
    title = media_title or "Unknown title"
    return (
        f"Media title: {title}\n"
        f"Target language code: {target_language_code}\n"
        f"Target language name: {target_language_name}\n\n"
        "Subtitle sample:\n"
        f"{sample_text}\n"
    )


def build_universe_classify_user_message(media_title: str | None) -> str:
    return f"Media title: {media_title or 'Unknown'}\nKnown universes: {', '.join(universe_keys())}\n"


def parse_terms_json(content: str) -> list[dict]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    raw_terms = data.get("terms") if isinstance(data, dict) else None
    if not isinstance(raw_terms, list):
        return []
    cleaned: list[dict] = []
    for item in raw_terms:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        if not source or not target:
            continue
        term_type = str(item.get("type") or item.get("term_type") or "other").strip().lower()
        policy = str(item.get("policy") or "keep").strip().lower()
        if term_type not in TERM_TYPES:
            term_type = "other"
        if policy not in POLICIES:
            policy = "keep"
        cleaned.append(
            {
                "source": source,
                "target": target,
                "term_type": term_type,
                "policy": policy,
            }
        )
        if len(cleaned) >= MAX_EXTRACT_TERMS:
            break
    # Prefer longer sources when duplicates collide after normalize.
    cleaned.sort(key=lambda t: len(t["source"]), reverse=True)
    deduped: list[dict] = []
    seen: set[str] = set()
    for term in cleaned:
        key = normalize_source(term["source"])
        if key in seen or len(key) < 2:
            continue
        seen.add(key)
        deduped.append(term)
    return deduped


def parse_universe_json(content: str) -> tuple[str | None, str | None, str | None]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None, None, None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    universe = str(data.get("universe") or "").strip().lower()
    if universe in {"", "none", "null"}:
        return None, None, None
    if universe == "other":
        other_key = str(data.get("other_key") or "").strip().lower()
        other_name = str(data.get("other_name") or "").strip()
        if not other_key:
            return None, None, None
        return slugify(other_key), other_name or other_key, None
    if universe in universe_keys():
        return universe, None, None
    return None, None, None


class GlossaryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_scopes(
        self,
        *,
        target_language: str | None = None,
        kind: str | None = None,
    ) -> list[GlossaryScopeRow]:
        stmt = select(GlossaryScopeRow).order_by(GlossaryScopeRow.kind, GlossaryScopeRow.display_name)
        if target_language:
            stmt = stmt.where(GlossaryScopeRow.target_language == target_language)
        if kind:
            stmt = stmt.where(GlossaryScopeRow.kind == kind)
        return list(self.db.scalars(stmt).all())

    def get_scope(self, scope_id: int) -> GlossaryScopeRow | None:
        return self.db.get(GlossaryScopeRow, scope_id)

    def list_terms(
        self,
        scope_id: int,
        *,
        status: str | None = None,
    ) -> list[GlossaryTermRow]:
        stmt = (
            select(GlossaryTermRow)
            .where(GlossaryTermRow.scope_id == scope_id)
            .order_by(GlossaryTermRow.source)
        )
        if status:
            stmt = stmt.where(GlossaryTermRow.status == status)
        return list(self.db.scalars(stmt).all())

    def list_suggested(
        self,
        *,
        target_language: str | None = None,
        limit: int = 200,
    ) -> list[tuple[GlossaryTermRow, GlossaryScopeRow]]:
        stmt = (
            select(GlossaryTermRow, GlossaryScopeRow)
            .join(GlossaryScopeRow, GlossaryTermRow.scope_id == GlossaryScopeRow.id)
            .where(GlossaryTermRow.status == "suggested")
            .order_by(GlossaryTermRow.updated_at.desc())
            .limit(limit)
        )
        if target_language:
            stmt = stmt.where(GlossaryScopeRow.target_language == target_language)
        return list(self.db.execute(stmt).all())

    def create_scope(
        self,
        *,
        kind: str,
        key: str,
        display_name: str,
        target_language: str,
        parent_scope_id: int | None = None,
        bazarr_series_id: int | None = None,
        bazarr_movie_id: int | None = None,
    ) -> GlossaryScopeRow:
        row = GlossaryScopeRow(
            kind=kind,
            key=key,
            display_name=display_name,
            target_language=target_language,
            parent_scope_id=parent_scope_id,
            bazarr_series_id=bazarr_series_id,
            bazarr_movie_id=bazarr_movie_id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_scope(
        self,
        scope_id: int,
        *,
        display_name: str | None = None,
        parent_scope_id: int | None = None,
        clear_parent: bool = False,
    ) -> GlossaryScopeRow:
        row = self.db.get(GlossaryScopeRow, scope_id)
        if not row:
            raise ValueError("Glossary scope not found")
        if display_name is not None:
            row.display_name = display_name
        if clear_parent:
            row.parent_scope_id = None
        elif parent_scope_id is not None:
            if parent_scope_id == scope_id:
                raise ValueError("Scope cannot be its own parent")
            row.parent_scope_id = parent_scope_id
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete_scope(self, scope_id: int) -> None:
        row = self.db.get(GlossaryScopeRow, scope_id)
        if not row:
            raise ValueError("Glossary scope not found")
        # Re-parent children to grandparent / none before delete.
        children = list(
            self.db.scalars(
                select(GlossaryScopeRow).where(GlossaryScopeRow.parent_scope_id == scope_id)
            ).all()
        )
        for child in children:
            child.parent_scope_id = row.parent_scope_id
            self.db.add(child)
        self.db.delete(row)
        self.db.commit()

    def clear_scopes(self, *, kind: str | None = None) -> int:
        """Delete glossary scopes (and cascaded terms), optionally filtered by kind."""
        scopes = self.list_scopes(kind=kind)
        if not scopes:
            return 0

        scope_ids = [scope.id for scope in scopes]
        scope_id_set = set(scope_ids)

        # Detach children that would lose their parent, preferring grandparent outside the delete set.
        for scope in scopes:
            children = list(
                self.db.scalars(
                    select(GlossaryScopeRow).where(GlossaryScopeRow.parent_scope_id == scope.id)
                ).all()
            )
            for child in children:
                if child.id in scope_id_set:
                    continue
                parent_id = scope.parent_scope_id
                while parent_id is not None and parent_id in scope_id_set:
                    parent = self.db.get(GlossaryScopeRow, parent_id)
                    parent_id = parent.parent_scope_id if parent else None
                child.parent_scope_id = parent_id
                self.db.add(child)

        self.db.execute(delete(GlossaryTermRow).where(GlossaryTermRow.scope_id.in_(scope_ids)))
        self.db.execute(
            update(GlossaryScopeRow)
            .where(GlossaryScopeRow.id.in_(scope_ids))
            .values(parent_scope_id=None)
        )
        self.db.execute(delete(GlossaryScopeRow).where(GlossaryScopeRow.id.in_(scope_ids)))
        self.db.commit()
        return len(scope_ids)

    def create_term(
        self,
        scope_id: int,
        *,
        source: str,
        target: str,
        term_type: str = "other",
        policy: str = "keep",
        status: str = "active",
        locked: bool = False,
        notes: str | None = None,
        source_origin: str = "user",
    ) -> GlossaryTermRow:
        scope = self.db.get(GlossaryScopeRow, scope_id)
        if not scope:
            raise ValueError("Glossary scope not found")
        source = source.strip()
        target = target.strip()
        if not source or not target:
            raise ValueError("source and target are required")
        if term_type not in TERM_TYPES:
            raise ValueError(f"Invalid term_type: {term_type}")
        if policy not in POLICIES:
            raise ValueError(f"Invalid policy: {policy}")
        if status not in STATUSES:
            raise ValueError(f"Invalid status: {status}")
        normalized = normalize_source(source)
        existing = self.db.scalar(
            select(GlossaryTermRow).where(
                GlossaryTermRow.scope_id == scope_id,
                GlossaryTermRow.source_normalized == normalized,
            )
        )
        if existing:
            raise ValueError("A term with this source already exists in the scope")
        row = GlossaryTermRow(
            scope_id=scope_id,
            source=source,
            source_normalized=normalized,
            target=target,
            term_type=term_type,
            policy=policy,
            status=status,
            locked=locked,
            source_origin=source_origin,
            notes=notes,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_term(
        self,
        term_id: int,
        *,
        source: str | None = None,
        target: str | None = None,
        term_type: str | None = None,
        policy: str | None = None,
        status: str | None = None,
        locked: bool | None = None,
        notes: str | None = None,
    ) -> GlossaryTermRow:
        row = self.db.get(GlossaryTermRow, term_id)
        if not row:
            raise ValueError("Glossary term not found")
        if source is not None:
            source = source.strip()
            if not source:
                raise ValueError("source cannot be empty")
            normalized = normalize_source(source)
            conflict = self.db.scalar(
                select(GlossaryTermRow).where(
                    GlossaryTermRow.scope_id == row.scope_id,
                    GlossaryTermRow.source_normalized == normalized,
                    GlossaryTermRow.id != term_id,
                )
            )
            if conflict:
                raise ValueError("A term with this source already exists in the scope")
            row.source = source
            row.source_normalized = normalized
        if target is not None:
            target = target.strip()
            if not target:
                raise ValueError("target cannot be empty")
            row.target = target
        if term_type is not None:
            if term_type not in TERM_TYPES:
                raise ValueError(f"Invalid term_type: {term_type}")
            row.term_type = term_type
        if policy is not None:
            if policy not in POLICIES:
                raise ValueError(f"Invalid policy: {policy}")
            row.policy = policy
        if status is not None:
            if status not in STATUSES:
                raise ValueError(f"Invalid status: {status}")
            row.status = status
        if locked is not None:
            row.locked = locked
        if notes is not None:
            row.notes = notes
        row.source_origin = "user"
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete_term(self, term_id: int) -> None:
        row = self.db.get(GlossaryTermRow, term_id)
        if not row:
            raise ValueError("Glossary term not found")
        self.db.delete(row)
        self.db.commit()

    def review_term(self, term_id: int, *, approve: bool, lock: bool = False) -> GlossaryTermRow:
        row = self.db.get(GlossaryTermRow, term_id)
        if not row:
            raise ValueError("Glossary term not found")
        if approve:
            row.status = "active"
            if lock:
                row.locked = True
            row.source_origin = "user"
        else:
            row.status = "rejected"
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_or_create_universe_scope(
        self,
        *,
        key: str,
        display_name: str,
        target_language: str,
    ) -> GlossaryScopeRow:
        existing = self.db.scalar(
            select(GlossaryScopeRow).where(
                GlossaryScopeRow.key == key,
                GlossaryScopeRow.target_language == target_language,
            )
        )
        if existing:
            return existing
        return self.create_scope(
            kind="universe",
            key=key,
            display_name=display_name,
            target_language=target_language,
        )

    def get_or_create_media_scope(
        self,
        *,
        media_type: str,
        media_title: str | None,
        target_language: str,
        bazarr_series_id: int | None,
        bazarr_movie_id: int | None,
        parent_scope_id: int | None,
    ) -> GlossaryScopeRow:
        if media_type == "episode" and bazarr_series_id is not None:
            existing = self.db.scalar(
                select(GlossaryScopeRow).where(
                    GlossaryScopeRow.kind == "series",
                    GlossaryScopeRow.bazarr_series_id == bazarr_series_id,
                    GlossaryScopeRow.target_language == target_language,
                )
            )
            if existing:
                if parent_scope_id and existing.parent_scope_id is None:
                    existing.parent_scope_id = parent_scope_id
                    self.db.add(existing)
                    self.db.commit()
                    self.db.refresh(existing)
                return existing
            title = media_title or f"Series {bazarr_series_id}"
            # Strip episode suffix if present ("Show - S01E01")
            series_name = re.split(r"\s+-\s+S\d+", title, maxsplit=1)[0].strip() or title
            return self.create_scope(
                kind="series",
                key=f"series:{bazarr_series_id}",
                display_name=series_name,
                target_language=target_language,
                parent_scope_id=parent_scope_id,
                bazarr_series_id=bazarr_series_id,
            )

        if bazarr_movie_id is not None:
            existing = self.db.scalar(
                select(GlossaryScopeRow).where(
                    GlossaryScopeRow.kind == "movie",
                    GlossaryScopeRow.bazarr_movie_id == bazarr_movie_id,
                    GlossaryScopeRow.target_language == target_language,
                )
            )
            if existing:
                if parent_scope_id and existing.parent_scope_id is None:
                    existing.parent_scope_id = parent_scope_id
                    self.db.add(existing)
                    self.db.commit()
                    self.db.refresh(existing)
                return existing
            title = media_title or f"Movie {bazarr_movie_id}"
            return self.create_scope(
                kind="movie",
                key=f"movie:{bazarr_movie_id}",
                display_name=title,
                target_language=target_language,
                parent_scope_id=parent_scope_id,
                bazarr_movie_id=bazarr_movie_id,
            )

        # Fallback title-based scope when Bazarr ids are missing.
        title = media_title or "Untitled"
        key = f"title:{slugify(title)}"
        existing = self.db.scalar(
            select(GlossaryScopeRow).where(
                GlossaryScopeRow.key == key,
                GlossaryScopeRow.target_language == target_language,
            )
        )
        if existing:
            if parent_scope_id and existing.parent_scope_id is None:
                existing.parent_scope_id = parent_scope_id
                self.db.add(existing)
                self.db.commit()
                self.db.refresh(existing)
            return existing
        kind = "series" if media_type == "episode" else "movie"
        return self.create_scope(
            kind=kind,
            key=key,
            display_name=title,
            target_language=target_language,
            parent_scope_id=parent_scope_id,
            bazarr_series_id=bazarr_series_id,
            bazarr_movie_id=bazarr_movie_id,
        )

    def scope_chain(self, leaf: GlossaryScopeRow) -> list[GlossaryScopeRow]:
        chain: list[GlossaryScopeRow] = []
        seen: set[int] = set()
        current: GlossaryScopeRow | None = leaf
        while current is not None and current.id not in seen:
            chain.append(current)
            seen.add(current.id)
            if current.parent_scope_id is None:
                break
            current = self.db.get(GlossaryScopeRow, current.parent_scope_id)
        chain.reverse()  # universe → … → leaf
        return chain

    def merge_terms_for_scopes(
        self,
        scopes: list[GlossaryScopeRow],
        *,
        include_suggested: bool = True,
    ) -> list[GlossaryTermView]:
        # Narrower scopes win: walk universe → leaf, later overrides earlier.
        by_source: dict[str, GlossaryTermView] = {}
        allowed = {"active", "suggested"} if include_suggested else {"active"}
        for scope in scopes:
            for term in scope.terms:
                if term.status not in allowed:
                    continue
                view = GlossaryTermView(
                    source=term.source,
                    target=term.target,
                    term_type=term.term_type,
                    policy=term.policy,
                    status=term.status,
                    locked=term.locked,
                    scope_id=scope.id,
                    scope_kind=scope.kind,
                    scope_name=scope.display_name,
                    term_id=term.id,
                )
                key = term.source_normalized
                existing = by_source.get(key)
                if existing and existing.locked and not view.locked:
                    continue
                by_source[key] = view
        # Prefer longer phrases first when injecting into prompts.
        terms = list(by_source.values())
        terms.sort(key=lambda t: len(t.source), reverse=True)
        return terms

    def merge_extracted_terms(
        self,
        scope: GlossaryScopeRow,
        extracted: list[dict],
        *,
        promote_if_empty: bool = True,
    ) -> int:
        """Merge LLM terms into scope. Returns count of newly suggested/created terms."""
        existing_terms = list(
            self.db.scalars(
                select(GlossaryTermRow).where(GlossaryTermRow.scope_id == scope.id)
            ).all()
        )
        by_norm = {t.source_normalized: t for t in existing_terms}
        has_active = any(t.status == "active" for t in existing_terms)
        default_status = "active" if promote_if_empty and not has_active else "suggested"
        created = 0
        for item in extracted:
            norm = normalize_source(item["source"])
            existing = by_norm.get(norm)
            if existing:
                if existing.locked or existing.status == "rejected":
                    continue
                # Do not overwrite user/active targets; only enrich empty/suggested from llm.
                if existing.source_origin == "user" or existing.status == "active":
                    continue
                existing.target = item["target"]
                existing.term_type = item["term_type"]
                existing.policy = item["policy"]
                self.db.add(existing)
                continue
            row = GlossaryTermRow(
                scope_id=scope.id,
                source=item["source"],
                source_normalized=norm,
                target=item["target"],
                term_type=item["term_type"],
                policy=item["policy"],
                status=default_status,
                locked=False,
                source_origin="llm",
            )
            self.db.add(row)
            by_norm[norm] = row
            created += 1
        self.db.commit()
        self.db.refresh(scope)
        return created

    async def detect_universe(
        self,
        *,
        media_title: str | None,
        client: Any | None,
        model: str | None,
    ) -> tuple[UniverseDef | None, str | None, int, int, int]:
        """Return (curated universe, custom key/name via LLM), plus token usage."""
        curated = match_universe(media_title)
        if curated:
            return curated, None, 0, 0, 0
        if not client or not model or not media_title:
            return None, None, 0, 0, 0
        try:
            result = await _provider_chat(
                client,
                model=batch_base_model(model),
                messages=[
                    {"role": "system", "content": UNIVERSE_CLASSIFY_SYSTEM},
                    {"role": "user", "content": build_universe_classify_user_message(media_title)},
                ],
                temperature=0,
            )
        except (AIProviderError, Exception) as exc:
            logger.warning("Universe classification failed: %s", exc)
            return None, None, 0, 0, 0
        key, other_name, _ = parse_universe_json(result.content)
        in_tok = result.input_tokens or 0
        out_tok = result.output_tokens or 0
        total = result.total_tokens or (in_tok + out_tok)
        if not key:
            return None, None, in_tok, out_tok, total
        from app.services.glossary_universes import UNIVERSES

        for universe in UNIVERSES:
            if universe.key == key:
                return universe, None, in_tok, out_tok, total
        display = other_name or key
        return None, f"{key}|{display}", in_tok, out_tok, total

    async def extract_terms(
        self,
        *,
        client: Any,
        model: str,
        media_title: str | None,
        target_language_code: str,
        target_language_name: str,
        document: SubtitleDocument,
    ) -> tuple[list[dict], Any]:
        sample = sample_document_text(document)
        result = await _provider_chat(
            client,
            model=batch_base_model(model),
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_extract_user_message(
                        media_title=media_title,
                        target_language_code=target_language_code,
                        target_language_name=target_language_name,
                        sample_text=sample or media_title or "",
                    ),
                },
            ],
            temperature=0,
        )
        return parse_terms_json(result.content), result

    async def prepare_for_translation(
        self,
        *,
        client: Any,
        model: str,
        media_type: str,
        media_title: str | None,
        target_language_code: str,
        target_language_name: str,
        bazarr_series_id: int | None,
        bazarr_movie_id: int | None,
        document: SubtitleDocument,
    ) -> ResolvedGlossary:
        in_tokens = 0
        out_tokens = 0
        total_tokens = 0

        curated, custom, u_in, u_out, u_total = await self.detect_universe(
            media_title=media_title,
            client=client,
            model=model,
        )
        in_tokens += u_in
        out_tokens += u_out
        total_tokens += u_total

        parent_id: int | None = None
        universe_key: str | None = None
        if curated:
            universe = self.get_or_create_universe_scope(
                key=curated.key,
                display_name=curated.display_name,
                target_language=target_language_code,
            )
            parent_id = universe.id
            universe_key = curated.key
        elif custom:
            key, _, display = custom.partition("|")
            universe = self.get_or_create_universe_scope(
                key=key,
                display_name=display or key,
                target_language=target_language_code,
            )
            parent_id = universe.id
            universe_key = key

        leaf = self.get_or_create_media_scope(
            media_type=media_type,
            media_title=media_title,
            target_language=target_language_code,
            bazarr_series_id=bazarr_series_id,
            bazarr_movie_id=bazarr_movie_id,
            parent_scope_id=parent_id,
        )

        extracted, extract_result = await self.extract_terms(
            client=client,
            model=model,
            media_title=media_title,
            target_language_code=target_language_code,
            target_language_name=target_language_name,
            document=document,
        )
        in_tokens += extract_result.input_tokens or 0
        out_tokens += extract_result.output_tokens or 0
        total_tokens += extract_result.total_tokens or (
            (extract_result.input_tokens or 0) + (extract_result.output_tokens or 0)
        )

        suggested_new = self.merge_extracted_terms(leaf, extracted, promote_if_empty=True)
        # Refresh leaf with terms after merge.
        leaf = self.db.get(GlossaryScopeRow, leaf.id) or leaf
        scopes = self.scope_chain(leaf)
        # Reload scopes with terms
        scopes = [self.db.get(GlossaryScopeRow, s.id) or s for s in scopes]
        terms = self.merge_terms_for_scopes(scopes, include_suggested=True)

        return ResolvedGlossary(
            leaf_scope=leaf,
            scopes=scopes,
            terms=terms,
            usage_input_tokens=in_tokens,
            usage_output_tokens=out_tokens,
            usage_total_tokens=total_tokens,
            suggested_new=suggested_new,
            universe_key=universe_key,
        )
