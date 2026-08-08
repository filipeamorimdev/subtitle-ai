# Translation pipeline

```text
Source SRT
  → parse
  → validate source
  → protect markup (<i>/<b>/<u> → <TAGn>)
  → batch (default 50) as [001] text blocks
  → OpenRouter chat completion
  → parse [ID] mapping (drop unexpected IDs)
  → validate IDs/count
  → targeted repair for missing/empty IDs (up to 2 attempts)
  → if still invalid: shrink batch (binary split) and retry
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

- Invalid model output after targeted repair + shrink-to-1 → job `failed`, nothing written
- Existing target file → job `skipped` (`target_exists`)
- Bazarr rescan error after write → job `completed` with `warning`

## Formats

v0.1 supports **SRT only**.
