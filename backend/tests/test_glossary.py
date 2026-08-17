"""Glossary memory unit tests."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.services.glossary import (
    GlossaryService,
    normalize_source,
    parse_terms_json,
    parse_universe_json,
    sample_document_text,
)
from app.services.glossary_universes import match_universe
from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.translation.openrouter.prompts import build_system_prompt, format_glossary_block


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def test_match_universe_curated():
    assert match_universe("WandaVision").key == "marvel"
    assert match_universe("The Batman").key == "dc"
    assert match_universe("Andor").key == "star-wars"
    assert match_universe("Random Indie Film") is None


def test_parse_terms_json_and_dedupe():
    content = """```json
{"terms":[
  {"source":"Captain America","target":"Capitão América","type":"character","policy":"localize"},
  {"source":"captain america","target":"Capitão América","type":"character","policy":"localize"},
  {"source":"S.H.I.E.L.D.","target":"S.H.I.E.L.D.","type":"organization","policy":"keep"}
]}
```"""
    terms = parse_terms_json(content)
    assert len(terms) == 2
    assert terms[0]["source"] == "Captain America"
    assert normalize_source("  Captain   America ") == "captain america"


def test_parse_universe_json():
    assert parse_universe_json('{"universe":"marvel"}')[0] == "marvel"
    assert parse_universe_json('{"universe":"none"}')[0] is None
    key, name, _ = parse_universe_json(
        '{"universe":"other","other_key":"Fast Saga","other_name":"Fast & Furious"}'
    )
    assert key == "fast-saga"
    assert name == "Fast & Furious"


def test_glossary_prompt_injection():
    class Term:
        source = "Iron Man"
        target = "Homem de Ferro"
        term_type = "character"
        policy = "localize"

    block = format_glossary_block([Term()])
    assert ' "Iron Man" → "Homem de Ferro" ' in block
    prompt = build_system_prompt("pt-PT", "Portuguese (Portugal)", glossary_terms=[Term()])
    assert "GLOSSARY" in prompt
    assert "Homem de Ferro" in prompt


def test_merge_prefers_child_and_respects_locks():
    db = _session()
    service = GlossaryService(db)
    universe = service.create_scope(
        kind="universe",
        key="marvel",
        display_name="Marvel",
        target_language="pt-PT",
    )
    series = service.create_scope(
        kind="series",
        key="series:1",
        display_name="WandaVision",
        target_language="pt-PT",
        parent_scope_id=universe.id,
        bazarr_series_id=1,
    )
    service.create_term(
        universe.id,
        source="Vision",
        target="Vision",
        term_type="character",
        policy="keep",
        status="active",
        locked=True,
    )
    service.create_term(
        series.id,
        source="Westview",
        target="Westview",
        term_type="place",
        policy="keep",
        status="active",
    )
    # Child override for unlocked parent would win; locked parent blocks child overwrite of same key.
    service.create_term(
        series.id,
        source="Vision",
        target="Visão",
        term_type="character",
        policy="localize",
        status="active",
    )

    series = service.get_scope(series.id)
    assert series is not None
    chain = service.scope_chain(series)
    merged = service.merge_terms_for_scopes(chain)
    by_source = {t.source: t for t in merged}
    assert by_source["Vision"].target == "Vision"
    assert by_source["Vision"].locked is True
    assert by_source["Westview"].target == "Westview"


def test_merge_extracted_promotes_then_suggests():
    db = _session()
    service = GlossaryService(db)
    scope = service.create_scope(
        kind="movie",
        key="movie:9",
        display_name="Iron Man",
        target_language="pt-PT",
        bazarr_movie_id=9,
    )
    created = service.merge_extracted_terms(
        scope,
        [
            {
                "source": "Tony Stark",
                "target": "Tony Stark",
                "term_type": "character",
                "policy": "keep",
            }
        ],
        promote_if_empty=True,
    )
    assert created == 1
    terms = service.list_terms(scope.id)
    assert terms[0].status == "active"

    created2 = service.merge_extracted_terms(
        scope,
        [
            {
                "source": "JARVIS",
                "target": "JARVIS",
                "term_type": "other",
                "policy": "keep",
            }
        ],
        promote_if_empty=True,
    )
    assert created2 == 1
    jarvis = next(t for t in service.list_terms(scope.id) if t.source == "JARVIS")
    assert jarvis.status == "suggested"

    # Locked term not overwritten
    tony = next(t for t in service.list_terms(scope.id) if t.source == "Tony Stark")
    tony.locked = True
    tony.target = "António Stark"
    db.add(tony)
    db.commit()
    service.merge_extracted_terms(
        scope,
        [
            {
                "source": "Tony Stark",
                "target": "Tony Stark",
                "term_type": "character",
                "policy": "keep",
            }
        ],
    )
    tony = next(t for t in service.list_terms(scope.id) if t.source == "Tony Stark")
    assert tony.target == "António Stark"


def test_sample_document_text_bounds():
    blocks = [
        SubtitleBlock(index=i, start="0", end="1", text=f"Line {i} " + ("x" * 20))
        for i in range(1, 120)
    ]
    doc = SubtitleDocument(format="srt", encoding="utf-8", blocks=blocks)
    sample = sample_document_text(doc, max_chars=500)
    assert len(sample) <= 500
    assert "Line 1" in sample


def test_review_term_api_helpers():
    db = _session()
    service = GlossaryService(db)
    scope = service.create_scope(
        kind="series",
        key="series:2",
        display_name="Loki",
        target_language="pt-PT",
        bazarr_series_id=2,
    )
    term = service.create_term(
        scope.id,
        source="TVA",
        target="AVT",
        status="suggested",
        source_origin="llm",
    )
    approved = service.review_term(term.id, approve=True, lock=True)
    assert approved.status == "active"
    assert approved.locked is True
    rejected = service.create_term(
        scope.id,
        source="Miss Minutes",
        target="Senhora Minutos",
        status="suggested",
    )
    rejected = service.review_term(rejected.id, approve=False)
    assert rejected.status == "rejected"


def test_summarize_counts_terms_and_pending_scopes():
    db = _session()
    service = GlossaryService(db)
    universe = service.create_scope(
        kind="universe",
        key="marvel",
        display_name="Marvel",
        target_language="pt-PT",
    )
    series = service.create_scope(
        kind="series",
        key="series:summary",
        display_name="WandaVision",
        target_language="pt-PT",
        parent_scope_id=universe.id,
        bazarr_series_id=11,
    )
    service.create_term(
        universe.id,
        source="Vision",
        target="Vision",
        status="active",
        locked=True,
    )
    service.create_term(
        series.id,
        source="Westview",
        target="Westview",
        status="suggested",
    )
    service.create_term(
        series.id,
        source="TVA",
        target="AVT",
        status="rejected",
    )
    summary = service.summarize(target_language="pt-PT")
    assert summary["scopes"] == 2
    assert summary["universes"] == 1
    assert summary["series"] == 1
    assert summary["movies"] == 0
    assert summary["active_terms"] == 1
    assert summary["locked_terms"] == 1
    assert summary["awaiting_review"] == 1
    assert summary["rejected"] == 1
    assert summary["pending_scopes"][0]["id"] == series.id
    assert summary["pending_scopes"][0]["suggested_count"] == 1
