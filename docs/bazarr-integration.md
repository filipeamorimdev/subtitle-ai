# Bazarr integration

Module: `backend/app/integrations/bazarr/`

## Endpoints used

| Operation | Path | Notes |
| --- | --- | --- |
| Connection test | `GET /api/system/status` | Falls back to probing movies if needed |
| Wanted movies | `GET /api/movies/wanted` | IDs + missing languages only (no path) |
| Wanted episodes | `GET /api/episodes/wanted` | IDs + missing languages only (no path) |
| Movie details | `GET /api/movies?radarrid[]=` | Path + subtitle files for enrichment |
| Episode details | `GET /api/episodes?episodeid[]=` | Path + subtitle files for enrichment |
| Movie rescan | `PATCH /api/movies?radarrid=&action=scan-disk` | Same as Bazarr UI **Scan Disk**; re-indexes sidecars from disk |
| Episode rescan | `PATCH /api/series?seriesid=&action=scan-disk` | No per-episode scan API; series Scan Disk runs `store_subtitles` for every episode |
| Download movie subtitle | `PATCH /api/movies/subtitles` | `radarrid`, `language` (code2), `forced`, `hi` — Bazarr queues search |
| Download episode subtitle | `PATCH /api/episodes/subtitles` | `seriesid`, `episodeid`, `language`, `forced`, `hi` — Bazarr queues search |

Authentication uses `apikey` query parameter when configured.

## Requesting a source subtitle

When a candidate has no usable source SRT, the UI exposes **Request EN** (or the first configured source language). That creates a background `request` job (`POST /api/candidates/request-subtitle`) which:

1. Asks Bazarr to search providers for that language.
2. Polls Bazarr (and disk) until an external SRT appears, or times out (~3 minutes).
3. Marks the job **completed** (found) or **failed** (not found), and opens the job detail page so progress is visible. The browser may also show a desktop notification when the search finishes.

## Candidate rules

A media item becomes a candidate when:

1. It appears on Bazarr wanted movies or episodes.
2. Missing languages are empty **or** compatible with the configured target (e.g. `pt` ↔ `pt-PT`).
3. Enrichment loads full movie/episode metadata (wanted lists omit `path` / `subtitles`).
4. Enrichment finds a usable **external SRT** source (Bazarr subtitle metadata preferred, then filesystem beside the media).

When no external source exists, candidates may still list **embedded** tracks:

- Bazarr entries with `path: null` are shown as embedded badges.
- `ffprobe` classifies codecs as text vs image when the media file is readable.
- **Extract** (background job) uses `ffmpeg` to dump extractable **text** tracks to a sidecar `.srt`. Blu-ray **PGS** image tracks are demuxed and OCR'd with Tesseract when it is installed (Docker image includes English, Portuguese, Spanish, French, German, and Italian).
- DVD VobSub / DVB image tracks are shown but not OCR'd.

`can_translate` is false when:

- `no_source` — no compatible source SRT (may still be extractable)
- `source_missing_on_disk` — metadata path not readable
- `target_exists` — target file already present

## Automatic fallback

When **Enable automatic fallback** is on in Settings, Subtitle AI periodically loads the same wanted candidates and, after the configured Bazarr grace period, enqueues `request` / `extract` / `translate` jobs automatically. Manual clicks still work and are prioritized.

After a successful translate write, Subtitle AI:

1. Calls movie/episode rescan (same endpoints as above).
2. Waits briefly and re-queries Bazarr.
3. Treats the job as verified when the target language is no longer missing (or appears in subtitle metadata).
4. If the file is valid but Bazarr still reports missing, completes with `bazarr_verify_failed` and does **not** translate again.

## Path mapping

Bazarr paths are rewritten through configured mappings before disk checks.

## Limitations

- Series Scan Disk is synchronous and indexes every episode in the show; a large library can take longer than a single-file write.
- DVD VobSub / DVB image subtitles are not OCR'd (Blu-ray PGS is).
- Source-language download is best-effort: Bazarr may find nothing if providers have no match, and English need not be in the item's profile for the specific-language download API.
- Automatic fallback is off by default and incurs OpenRouter costs when enabled.
