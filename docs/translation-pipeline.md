# Translation pipeline

```text
Source SRT
  → parse
  → validate source
  → prepare glossary (detect universe + extract/merge terms)
  → protect markup (<i>/<b>/<u> → <TAGn>)
  → batch (default 50) as [001] text blocks
  → ModelRouter selects eligible models (strategy + cost/budget)
  → OpenRouter chat completion (glossary injected into system prompt)
    → parse [ID] mapping (drop unexpected IDs)
    → validate IDs/count
    → targeted repair for missing/empty IDs (up to 2 attempts)
    → if still invalid: shrink batch (binary split) and retry
    → on timeout/429/5xx: resume remaining batches on the next eligible model
  → restore markup
  → reconstruct SRT (original timestamps/IDs)
  → validate full document
  → atomic write (temp → fsync → rename)
  → Bazarr rescan
```

## Glossary memory

- Scopes: `universe` → `series` / `movie` (narrower overrides parent)
- Terms: source → target + type/policy; statuses `active` | `suggested` | `rejected`
- Locked user terms are never overwritten by LLM extraction
- First extraction on an empty scope promotes terms to `active`; later new terms are `suggested` for review
- Universe detection: curated title markers first, LLM classification fallback
- UI: Settings → Glossary (`/settings/glossary`) for edit/lock and suggested-term review

## Prompt policy

- Translate dialogue only
- Preserve IDs, ordering, one-to-one blocks
- Follow glossary target forms consistently
- No markdown/timestamps in model output
- Explicit European Portuguese instructions when target is PT-PT
- User prompt states exact block count and expected ID list

## Failure behavior

- Invalid model output after targeted repair + shrink-to-1 → job `failed`, nothing written (stays on that model; no pool fallback)
- Timeout / 429 / 5xx → next eligible model; successful batches are not resent
- Cost/budget block → `blocked_by_cost_policy` (not retried by automatic fallback)
- Existing target file → job `skipped` (`target_exists`)
- Bazarr rescan error after write → job `completed` with `warning`

## Formats

Output is **SRT**. Source SRT may come from disk, Bazarr, ffmpeg text-track extract, or Tesseract OCR of Blu-ray PGS image tracks.
