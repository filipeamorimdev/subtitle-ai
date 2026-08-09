# Architecture

Subtitle AI is a single Docker service that combines:

- **FastAPI** REST API and static Vue UI
- **SQLite** persistence under `/config`
- An **asyncio worker** that processes one translation job at a time
- Integrations for **Bazarr** (wanted detection + rescan) and **OpenRouter** (chat completions)

## Boundaries

```text
Bazarr wanted lists
        ↓
CandidateService (detect only)
        ↓
User clicks Translate
        ↓
JobService + Worker
        ↓
SRT parse / markup protect
        ↓
OpenRouterTranslationService (batched)
        ↓
Validation
        ↓
Atomic SRT write
        ↓
Bazarr rescan (best effort)
```

## Components

| Area | Responsibility |
| --- | --- |
| `integrations/bazarr` | HTTP client, wanted normalization, rescan |
| `services/candidates` | Build UI candidates; never enqueue jobs |
| `subtitles` | Parse, markup, validate, write SRT |
| `translation/openrouter` | Client + batch prompt/response parsing |
| `jobs` | Queue rows, locking, worker loop |
| `services/settings` | Encrypted secrets, public masked settings |

## Persistence

- `settings` — singleton configuration
- `jobs` — user-triggered translation work
- `translation_cache` — completed hash/language/model triples
- `glossary_scopes` / `glossary_terms` — persistent term memory (universe/series/movie)

Candidates are fetched on demand from Bazarr (not a primary table).

## Non-goals (v0.1)

- Automatic schedulers
- Multiple AI providers
- Non-SRT formats
- Whisper/ASR
