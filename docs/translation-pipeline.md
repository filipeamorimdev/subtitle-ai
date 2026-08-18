# Translation pipeline

```text
Source SRT
  → parse
  → validate source
  → protect markup (<i>/<b>/<u> → <TAGn>)
  → batch (default 50) as [001] text blocks
  → ModelRouter selects eligible models (strategy + cost/budget)
  → OpenRouter chat completion
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

## Prompt policy

- Translate dialogue only
- Preserve IDs, ordering, one-to-one blocks
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
