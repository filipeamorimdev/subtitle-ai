# AI model routing

Subtitle AI v0.3-alpha1 routes translation jobs across **configured provider models** using explicit pools and cost policy. Adaptive ranking on the AI Overview page is **display-only** and never used for routing.

Canonical model identity is **`(provider_id, model_id)`**. In this milestone only **OpenRouter** is registered.

Configure credentials under **Settings → Models → Providers**. Configure pools, strategy, budgets, and diagnostic exchange logging under **Settings → Models → Models & Routing**. General Settings contains application options (Bazarr, languages, automation, media paths, concurrency). `openrouter_model` remains a compatibility field (first enabled preference).

See also [ai-providers.md](ai-providers.md).

## Pools and priority

Two pools:

- **Free** — models classified as $0 / $0 from catalog prices
- **Paid** — any model with a positive prompt or completion price

Each pool has an explicit order. Disabled models are skipped. Duplicate `model_id` values are not allowed. A catalog-priced paid model cannot be added to the free pool (and vice versa).

## Routing strategies

| Strategy | Order |
| --- | --- |
| `free_only` | Free pool only |
| `paid_only` | Paid pool only |
| `free_first` | Free pool, then paid **if** `allow_paid_fallback` is on |
| `paid_first` | Paid pool, then free **if** `allow_free_fallback` is on |

`allow_paid_fallback` defaults to **off**. Existing installs that only had `openrouter_model` are seeded into one pool (`:free` suffix → `free_only`, otherwise `paid_only`) and never get paid fallback enabled automatically.

User priority → routing policy → cost/compatibility guards → selected model. Adaptive rank never reorders pools.

## Catalog, pricing, compatibility

OpenRouter `GET /models` is cached for **6 hours**. If a refresh fails, the last catalog is kept and marked **stale**.

- Missing prices are **unknown**, never treated as $0 / free
- Unknown-priced catalog entries are skipped unless `allow_unknown_pricing` is on
- A configured model is still eligible if the catalog has not been fetched yet
- Compatibility requires text in/out (when modalities are advertised) and enough context for the current batch size
- Cache/dedupe key remains `sha256(source_hash|target_language|model)` — a fallback model is a different cache entry

## Technical fallback

On timeout, 429, or 5xx the job tries the **next eligible model** and does **not** resend batches that already succeeded.

Validation failures stay on the current model’s repair/shrink path. They do not burn the rest of the pool.

## Conservative cost estimate

Per-job and monthly budget gates use a **conservative** estimate, not a bare subtitle character count:

```text
subtitle_tokens   = max(1, char_count // 4)
input_tokens      = subtitle_tokens + 2000 (system/batch) + 1500 (glossary)
output_tokens     = subtitle_tokens + 15% repair allowance
estimated_cost    = price(input_tokens, output_tokens)
conservative_cost = estimated_cost × 1.25
```

Actual billed cost is still recorded after each request from OpenRouter usage / pricing snapshots in `ai_usage_records`.

## Cost and budget

Money is stored as integer **micro-USD**.

- **Per-job cap** skips paid models whose **conservative** estimate exceeds `maximum_cost_per_job`
- **Monthly budget** reservations are serialized with a **process-wide lock** and committed before the AI call, so concurrent jobs in the same process cannot overspend remaining budget
- The lock is **not** a distributed lock. The supported deployment is a single application process and a single SQLite database (Docker). Multiple independent processes sharing one SQLite file is not a supported concurrency model for budget reservations
- Free models skip reservation
- Manual jobs may bypass the monthly cap only when `allow_manual_budget_override` is on; automatic jobs never bypass
- Blocked jobs use reason `blocked_by_cost_policy`, create **no** AI request, and are **not** retried by automatic fallback

## Adaptive ranking (display-only)

Ranking uses only production translation operations:

`translation`, `translation_retry`, `translation_repair`

Excluded: `model_test`, `glossary_universe`, `glossary_extract`, and any other non-translation op.

Score philosophy:

```text
quality      = clean% − 25×repair_rate − 50×validation_failure_rate
reliability  = 100 × (1 − technical_failure_rate)
adaptive     = 0.50×Q + 0.25×C + 0.20×S + 0.05×R
```

Confidence: under 10 samples → insufficient (no rank); 10–24 low; 25–99 medium; 100+ high.

Model tests still count toward budget and usage analytics but never influence adaptive production ranking.

## Authoritative usage

`ai_usage_records` is the historical AI cost source (dashboard, usage page, job stats). Rows store the pricing snapshot from the request. Job exchange logs remain for debug detail and are not used to reprice history from a live catalog.

Full request/response bodies remain opt-in under **Settings → Models → Providers** (`openrouter_log_full_exchanges`, default off).

## Privacy

Usage and routing tables store model ids, tokens, cost snapshots, latency, and outcomes. They do not store subtitle text. Full request/response bodies remain opt-in on the job JSONL log.

## Paid-fallback warning

Turning on `allow_paid_fallback` can incur OpenRouter charges as soon as every free candidate fails. Keep it off unless you intend that.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `no_compatible_model` | Add a compatible model to the pool used by the current strategy |
| `blocked_by_cost_policy` | Raise the per-job cap or monthly budget, or use a cheaper/free model |
| Catalog **STALE** | Refresh on Settings → Models; jobs still use last-known metadata |
| Existing install stopped translating | Confirm the seeded preference is still enabled; catalog miss should not block it |
| Adaptive rank “insufficient” | Display-only; needs ≥10 translation samples and does not change routing |
