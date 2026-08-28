# Architecture

Subtitle AI is a single Docker service that combines:

- **FastAPI** REST API and static Vue UI
- **SQLite** persistence under `/config`
- An **asyncio worker** that processes jobs with configurable per-kind concurrency (default: one translate, one extract, one request, and one transcribe at a time)
- An optional **automatic scanner** that enqueues the same jobs when automatic fallback is enabled
- Integrations for **Bazarr** (wanted detection + rescan + verify) and an **AI provider layer** (OpenRouter adapter in v0.3-alpha1)
- **ffmpeg** for text subtitle extract, PGS demux, and audio extract for ASR; **Tesseract** for PGS OCR; **faster-whisper** (optional OpenAI Whisper API) for manual audio transcription

## Boundaries

```text
Bazarr library / wanted lists
        ↓
MediaItem + CandidateService
        ↓
   ┌────┴────┐
   │         │
Manual    AutomaticScanner    OperatorChat
request        ↓                    │
   │      Grace + FallbackPlanner   │ tools
   │         ↓                      │
   └────┬────┴──────────────────────┘
        ↓
LocalizationTask + TaskPlanner
        ↓
JobService + Worker
        ↓
LocalizationPipeline
        ├── SourceResolver
        ├── SubtitlePipeline (translate / extract / transcribe)
        └── DubbingPipeline
        ↓
ModelRouter → AIProvider (OpenRouter)
        ↓
Validate + Bazarr verify → task completed
```

The dashboard **operator chat** is not a second orchestrator. It calls a whitelist of tools (`search_media`, `ensure_media`, `create_localization_task`, …) that wrap the same services as the REST UI. OpenRouter emits structured `tool_calls`; assistant prose never creates jobs.

See [localization-tasks.md](localization-tasks.md) for the media-centric task model and [ai-providers.md](ai-providers.md) for the BYOAI provider abstraction.

## Components

| Area | Responsibility |
| --- | --- |
| `media/` | MediaRef, BazarrMediaProvider, MediaItem identity cache, process runner |
| `localization/` | LocalizationTask service, state machine, TaskPlanner, LocalizationPipeline |
| `localization/operator` | Dashboard chat agent loop (OpenRouter tools → operator_tools) |
| `localization/operator_tools` | Whitelisted goal-level tools (search, create task, dub, …) |
| `localization/source_resolver` | Scores subtitle / embedded / OCR / transcript sources (no hard gates) |
| `localization/transcription` | Audio track selection, ASR providers, chunking, subtitle formatter |
| `localization/audio` | Demucs vocals/accompaniment separation used by background-preserved dubbing, with optional debug traces |
| `localization/dubbing` | SpeechSegments, TTS providers, timing, PCM timeline, mux |
| `languages/` | Canonical language catalog + normalization |
| `integrations/bazarr` | HTTP client, wanted normalization, rescan, target presence check |
| `services/candidates` | Build UI candidates; never enqueue jobs |
| `services/fallback` | Observation store, grace period, automatic next-action planner |
| `jobs/scanner` | Background loop; no-op when automatic fallback is disabled |
| `subtitles` | Parse, markup, validate, write SRT; PGS demux + Tesseract OCR; audio transcription (Whisper) |
| `ai/` | Generic provider types, registry, OpenRouter adapter, credentials |
| `translation/` | Provider-agnostic TranslationService + prompts; OpenRouter HTTP client |
| `services/model_router` | Deterministic free/paid pool selection and cost gating |
| `services/model_catalog` | Provider-aware catalog cache, pricing freshness, compatibility |
| `services/ai_budget` | Monthly budget + process-wide SQLite reservations |
| `services/ai_usage` | Authoritative per-request cost/usage snapshots (`provider_id`, `request_id`) |
| `services/ai_ranking` | Display-only adaptive ranking (never used for routing) |
| `jobs` | Queue rows, locking, worker loop |
| `services/settings` | Encrypted secrets, public masked settings |

## Persistence

- `settings` — singleton configuration (including automatic fallback toggles, routing strategy, budget)
- `media_items` — lightweight media identity cache (Bazarr IDs + display metadata)
- `localization_tasks` — user-facing localization goals (subtitles; audio reserved for later)
- `jobs` — translation / extract / request / transcribe work (`trigger_type` = manual \| automatic); optional `task_id`; includes `provider_id`
- `observed_candidates` — first-seen / grace-period state for automation
- `translation_cache` — completed hash/language/provider/model tuples
- `ai_provider_accounts` — encrypted provider credentials (OpenRouter in alpha1)
- `ai_model_preferences` — free/paid model pools keyed by `(provider_id, model_id)`
- `ai_model_catalog_cache` — per-provider catalog snapshot (6-hour freshness)
- `openrouter_model_preferences` / `openrouter_catalog_cache` — legacy tables kept for rollback
- `ai_usage_records` — **authoritative** per-request tokens, pricing snapshots, `request_id`, `cost_source`
- `ai_budget_reservations` — in-flight monthly budget holds
- `ai_routing_events` — recent routing/fallback decisions with provider chain fields

Candidates for the UI are still fetched on demand from Bazarr wanted lists. Media items are persisted when searched or used in a task. Observation rows exist only to support automatic fallback.

Job OpenRouter JSONL logs remain debug detail. They are not a competing historical accounting system and are never used to reprice history from the live catalogue.

## Deployment model

The supported deployment is:

```text
one Subtitle AI application process
        ↓
one SQLite database under /config
        ↓
Docker (single container)
```

Budget reservations use a **process-wide lock** around check / insert / commit. That is concurrency-safe for concurrent workers and API activity **inside the same Python process**. It is not a distributed lock. Running multiple independent application processes against the same SQLite database is **not** a supported concurrency model for budget reservations.

## Dashboard vs Settings

| Surface | Answers |
| --- | --- |
| Dashboard (Ops) | What is Subtitle AI doing right now? Pipeline stages, interventions, live work, automation. |
| Dashboard (AI) | Detailed AI observability (Overview / Usage) on the same page. |
| Settings | How is Subtitle AI configured? General (incl. language), Providers, Models. |

Provider credentials live under **Settings → Providers**. Pools, strategy, and budgets live under **Settings → Models**. Adaptive ranking on Overview is display-only and never reorders pools or enables paid fallback.

## Automatic fallback

Off by default. When enabled:

1. Scanner wakes on `automatic_scan_interval_minutes`.
2. Wanted items are observed; `first_seen_at` survives restarts.
3. After `bazarr_grace_period_minutes`, the planner enqueues at most one next job (translate, extract, or request).
4. Automatic extract/request success chains into translate while the toggle remains on.
5. Manual jobs are claimed before automatic jobs of the same kind.

## Audio transcription

`SourceResolver` scores available sources. A French sidecar does **not** block transcription when the preferred source language is English. Transcription is selected when it outscores other-language subtitles and non-preferred embedded tracks.

`AudioTrackSelector` picks the dialogue stream from ffprobe metadata (language, default, commentary/AD penalties). `TranscriptionService` extracts that stream, chunks by duration with overlap, runs an `ASRProvider` (`faster-whisper` or OpenAI), and `SubtitleFormatter` builds readable SRT cues from word timestamps. Detected language and confidence are stored; English is never assumed when detection is missing.

Jobs record pipeline decisions in the job JSONL event log and task `metadata_json["pipeline"]`.

## TTS dubbing

When a target-language SRT already exists beside the media file, the media page can start a **dub** job. `DubbingPipeline` maps labelled subtitle cues to `SpeechSegment`s and can assign local Chatterbox delivery profiles per speaker; unlabelled cues fall back to the target-language natural profile. It synthesizes dialogue with bounded timing adaptation, builds a timeline, and normally calls `AudioSeparationService` (Demucs two-stem vocals) before sidechain-mixing translated dialogue into the retained music/ambience/effects. The generated 48 kHz stereo dub is the default muxed audio track and the original audio is preserved as an alternate. `voiceover_preview` keeps the prior speech-only path. The source video is never overwritten. Completion is verified with ffprobe. Dubbing is **not** auto-enqueued by TaskPlanner or automatic fallback.

## Isolated audio separation

`AudioSeparationService` splits the selected source audio stream into a dialogue/vocals stem and a background/accompaniment stem using local Demucs (`--two-stems vocals`). `DubbingPipeline` uses the background stem in `background_preserved` mode. Optional traces live under `/config/debug/audio-separation/<task-id>/trace.log` when `SUBTITLE_AI_DEBUG_TRACE=true`.

## Upgrade from v0.1 / v0.2.1

On startup `init_db()` adds missing tables/columns, seeds legacy preferences when pools are empty, and runs an idempotent `migrate_legacy_openrouter()` that copies credentials, preferences, catalog cache, and backfills `provider_id='openrouter'` on usage/routing/jobs/cache. Existing jobs and settings are preserved. Paid fallback stays off. Historical cost rows are not rewritten.

## Non-goals (this milestone)

- Additional concrete translation providers beyond OpenRouter (OpenAI/Anthropic SDKs, etc.)
- Adaptive **routing** (ranking is display-only)
- Non-SRT formats
- In-place dub mux / replacing original audio tracks
- Speaker diarization / multiple TTS voices / voice cloning
- Mixing separated background stems into the final dubbed file
- WhisperX alignment (provider slot only)
