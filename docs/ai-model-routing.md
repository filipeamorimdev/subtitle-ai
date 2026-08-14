# AI model routing

Subtitle AI v0.2 routes translation jobs across **configured OpenRouter models** using explicit pools and cost policy. Adaptive ranking on the AI Overview page is **display-only** and never used for routing.

Configure this under **AI → Models**. Settings still stores `openrouter_model` as a compatibility field (first enabled preference).

## Pools and priority

Two pools:

- **Free** — models classified as $0 / $0 from catalog prices
- **Paid** — any model with a positive prompt or completion price

Each pool has an explicit order. Disabled models are skipped. Duplicate `model_id` values are not allowed.

## Routing strategies

| Strategy | Order |
| --- | --- |
| `free_only` | Free pool only |
| `paid_only` | Paid pool only |
| `free_first` | Free pool, then paid **if** `allow_paid_fallback` is on |
| `paid_first` | Paid pool, then free **if** `allow_free_fallback` is on |

`allow_paid_fallback` defaults to **off**. Existing installs that only had `openrouter_model` are seeded into one pool (`:free` suffix → `free_only`, otherwise `paid_only`) and never get paid fallback enabled automatically.

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

## Cost and budget

Money is stored as integer **micro-USD**.

- **Per-job cap** skips paid models whose estimate exceeds `maximum_cost_per_job`
- **Monthly budget** uses SQLite reservations so concurrent jobs cannot overspend the remaining limit
- Free models skip reservation
- Manual jobs may bypass the monthly cap only when `allow_manual_budget_override` is on; automatic jobs never bypass
- Blocked jobs use reason `blocked_by_cost_policy` and are **not** retried by automatic fallback

## Privacy

Usage and routing tables store model ids, tokens, cost snapshots, latency, and outcomes. They do not store subtitle text. Full request/response bodies remain opt-in on the job JSONL log.

## Paid-fallback warning

Turning on `allow_paid_fallback` can incur OpenRouter charges as soon as every free candidate fails. Keep it off unless you intend that.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `no_compatible_model` | Add a compatible model to the pool used by the current strategy |
| `blocked_by_cost_policy` | Raise the per-job cap or monthly budget, or use a cheaper/free model |
| Catalog **STALE** | Refresh on AI → Models; jobs still use last-known metadata |
| Existing install stopped translating | Confirm the seeded preference is still enabled; catalog miss should not block it |
| Adaptive rank “insufficient” | Display-only; needs ≥10 samples and does not change routing |
