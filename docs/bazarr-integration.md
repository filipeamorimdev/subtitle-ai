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
| Movie rescan | `GET /api/movies/scan?radarrid=` | Best effort; alternate POST subtitles action tried on failure |
| Episode rescan | `GET /api/episodes/scan?episodeid=` | Best effort; alternate POST tried on failure |

Authentication uses `apikey` query parameter when configured.

## Candidate rules

A media item becomes a candidate when:

1. It appears on Bazarr wanted movies or episodes.
2. Missing languages are empty **or** compatible with the configured target (e.g. `pt` ↔ `pt-PT`).
3. Enrichment loads full movie/episode metadata (wanted lists omit `path` / `subtitles`).
4. Enrichment finds a usable **external SRT** source (Bazarr subtitle metadata preferred, then filesystem beside the media).

When no external source exists, candidates may still list **embedded** tracks:

- Bazarr entries with `path: null` are shown as embedded badges.
- `ffprobe` classifies codecs as text vs image when the media file is readable.
- **Extract** (background job) uses `ffmpeg` to dump extractable text tracks to a sidecar `.srt`.
- Image-based tracks (PGS / VobSub) are shown but not extractable in v0.1.

`can_translate` is false when:

- `no_source` — no compatible source SRT (may still be extractable)
- `source_missing_on_disk` — metadata path not readable
- `target_exists` — target file already present

## Path mapping

Bazarr paths are rewritten through configured mappings before disk checks.

## Limitations

- Rescan endpoint names differ across Bazarr versions; failures after a successful write mark the job completed with a warning.
- Embedded/image subtitles are ignored in v0.1.
- Detection does **not** create translation jobs automatically.
