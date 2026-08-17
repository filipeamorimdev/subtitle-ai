# AI Providers (BYOAI)

Subtitle AI **v0.3-alpha1** introduces a provider-agnostic AI layer. Only **OpenRouter** is implemented in this milestone.

## What BYOAI means

Bring Your Own AI means you supply **provider API keys** (for example an OpenRouter key). It does **not** mean a normal ChatGPT or Claude subscription can be used as API access.

Future milestones may add OpenAI and Anthropic API providers. They are **not** available in alpha1 — do not expect credential UI for them yet.

## Architecture

```text
Subtitle AI
    │
Model Router  (provider + model candidates)
    │
AI Provider interface
    │
OpenRouterProvider  ← only concrete provider in alpha1
    │
OpenRouter API
```

Translation, glossary, routing, ranking, and the AI dashboard depend on generic types (`AIModel`, `AIResponse`, `AIProvider`). HTTP details, auth headers, `:batch` support, and OpenRouter JSON stay inside the adapter.

## Model identity

Every model reference uses **`(provider_id, model_id)`**, not `model_id` alone.

This applies to preferences, catalog cache, usage records, routing events, rankings, translation cache, jobs, UI, and API payloads.

Example: `openrouter` + `meta-llama/llama-3.3-70b-instruct:free`.

## Credentials

- Stored in `ai_provider_accounts` (encrypted with the same Fernet secret as Bazarr keys).
- Never returned in plaintext through the API.
- UI shows masked status such as `••••••6266`.
- Legacy `settings.openrouter_api_key_encrypted` remains for compatibility; new code prefers the provider account and falls back to the legacy column if needed.
- Credential rotation replaces the encrypted key and invalidates cached connection health. There is **no** dual-key grace period.

Manage keys under **Settings → AI providers**. Models no longer hosts the API-key form.

## Catalog and pricing

- Cache: `ai_model_catalog_cache` (one row per provider).
- Freshness is derived from `fetched_at`: `unknown` / `fresh` / `stale` (6-hour TTL).
- Concurrent refresh is coordinated with a per-provider in-flight lock and a 5-minute minimum refresh interval.
- Historical cost always uses **request-time** pricing snapshots, not today's catalogue.

## Routing and budgets

Routing policies (`free_first`, `paid_only`, …), user priority, cost caps, and the monthly budget are unchanged and **provider-agnostic**. The monthly budget is global across providers.

Adaptive ranking remains **display-only** and never changes selection. It ranks `(provider_id, model_id)`.

## Migration from v0.2.1

On `init_db()`:

1. Legacy OpenRouter API key ciphertext → `ai_provider_accounts(provider_id=openrouter)`
2. `openrouter_model_preferences` → `ai_model_preferences(provider_id=openrouter)`
3. Historical usage / routing / jobs / translation cache → `provider_id=openrouter`
4. Catalog cache copied when present

Migration is idempotent and does not contact the network or spend money. Legacy tables/columns are **not** deleted in alpha1.

## Adding a future provider

Implement `AIProvider`, register it in the production registry, add credential handling, and populate the catalog. The translation pipeline should not need structural changes.

## Security

- API keys encrypted at rest
- Exchange / audit metadata is allowlisted (no prompts, secrets, or full subtitle content unless full exchange logging is explicitly enabled)
- Connection-test results are cached briefly (5 minutes) to avoid spamming provider APIs
