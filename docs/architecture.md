# Architecture

Subtitle AI is a single Docker service that combines:

- **FastAPI** REST API and static Vue UI
- **SQLite** persistence under `/config`
- An **asyncio worker** that processes jobs with configurable per-kind concurrency (default: one translate, one extract, and one request at a time)
- An optional **automatic scanner** that enqueues the same jobs when automatic fallback is enabled
- Integrations for **Bazarr** (wanted detection + rescan + verify) and **OpenRouter** (chat completions)

## Boundaries

```text
Bazarr wanted lists
        ↓
CandidateService (detect only)
        ↓
   ┌────┴────┐
   │         │
Manual    AutomaticScanner (opt-in)
clicks         ↓
   │      Grace period + FallbackPlanner
   │         ↓
   └────┬────┘
        ↓
JobService + Worker
        ↓
SRT parse / markup protect
        ↓
ModelRouter (pools + cost policy)
        ↓
OpenRouterTranslationService (batched; technical fallback)
        ↓
Validation
        ↓
Atomic SRT write
        ↓
Bazarr rescan + verify (best effort)
```

## Components

| Area | Responsibility |
| --- | --- |
| `integrations/bazarr` | HTTP client, wanted normalization, rescan, target presence check |
| `services/candidates` | Build UI candidates; never enqueue jobs |
| `services/fallback` | Observation store, grace period, automatic next-action planner |
| `jobs/scanner` | Background loop; no-op when automatic fallback is disabled |
| `subtitles` | Parse, markup, validate, write SRT |
| `translation/openrouter` | Client + batch prompt/response parsing |
| `services/model_router` | Deterministic free/paid pool selection and cost gating |
| `services/model_catalog` | Cached OpenRouter catalog, pricing tier, compatibility |
| `services/ai_budget` | Monthly budget + SQLite reservations |
| `jobs` | Queue rows, locking, worker loop |
| `services/settings` | Encrypted secrets, public masked settings |

## Persistence

- `settings` — singleton configuration (including automatic fallback toggles, routing strategy, budget)
- `jobs` — translation / extract / request work (`trigger_type` = manual \| automatic)
- `observed_candidates` — first-seen / grace-period state for automation
- `translation_cache` — completed hash/language/model triples
- `glossary_scopes` / `glossary_terms` — persistent term memory (universe/series/movie)
- `openrouter_model_preferences` — free/paid model pools and priority
- `openrouter_catalog_cache` — 6-hour OpenRouter catalog snapshot
- `ai_usage_records` — per-request tokens, cost snapshots, outcomes
- `ai_budget_reservations` — in-flight monthly budget holds
- `ai_routing_events` — recent routing/fallback decisions

Candidates for the UI are still fetched on demand from Bazarr. Observation rows exist only to support automatic fallback.

## Automatic fallback

Off by default. When enabled:

1. Scanner wakes on `automatic_scan_interval_minutes`.
2. Wanted items are observed; `first_seen_at` survives restarts.
3. After `bazarr_grace_period_minutes`, the planner enqueues at most one next job (translate, extract, or request).
4. Automatic extract/request success chains into translate while the toggle remains on.
5. Manual jobs are claimed before automatic jobs of the same kind.

## Non-goals (this milestone)

- Multiple AI providers
- Adaptive **routing** (ranking is display-only)
- Non-SRT formats
- Whisper/ASR
- TTS / dubbing / media muxing
