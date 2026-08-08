# Bazarr integration

Module: `backend/app/integrations/bazarr/`

## Endpoints used

| Operation | Path | Notes |
| --- | --- | --- |
| Connection test | `GET /api/system/status` | Falls back to probing movies if needed |
| Wanted movies | `GET /api/movies/wanted` | Missing movie subtitles |
| Wanted episodes | `GET /api/episodes/wanted` | Missing episode subtitles |
| Movie rescan | `GET /api/movies/scan?radarrid=` | Best effort; alternate POST subtitles action tried on failure |
| Episode rescan | `GET /api/episodes/scan?episodeid=` | Best effort; alternate POST tried on failure |

Authentication uses `apikey` query parameter when configured.

## Candidate rules

A media item becomes a candidate when:

1. It appears on Bazarr wanted movies or episodes.
2. Missing languages are empty **or** compatible with the configured target (e.g. `pt` ↔ `pt-PT`).
3. Enrichment finds a usable **external SRT** source (Bazarr subtitle metadata preferred, then filesystem beside the media).

`can_translate` is false when:

- `no_source` — no compatible source SRT
- `source_missing_on_disk` — metadata path not readable
- `target_exists` — target file already present

## Path mapping

Bazarr paths are rewritten through configured mappings before disk checks.

## Limitations

- Rescan endpoint names differ across Bazarr versions; failures after a successful write mark the job completed with a warning.
- Embedded/image subtitles are ignored in v0.1.
- Detection does **not** create translation jobs automatically.
