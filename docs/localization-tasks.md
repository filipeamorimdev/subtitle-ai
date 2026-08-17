# Localization tasks (v0.3)

v0.3 introduces a **media-centric** localization model on top of the existing job/worker stack.

> **Scope:** subtitle localization tasks and on-demand subtitle requests.  
> **Audio localization / dubbing is not implemented** in this version (the capability enum is reserved for later).

## Mental model

```text
MediaItem
    └── LocalizationTask   (user-facing goal)
            └── Job / execution   (translate | extract | request)
```

Users ask for an outcome (“The Matrix → Portuguese (Portugal) subtitles”).  
Subtitle AI decides how to fulfill it using the existing source mechanisms and AI routing.

Historical jobs without `task_id` remain valid legacy execution history.

## LocalizationTask vs execution

| | LocalizationTask | Job (execution) |
| --- | --- | --- |
| Represents | Desired outcome for one media + language + capability | One concrete unit of work |
| Primary UI | Media list, media file page, dashboard | History on the media file page; job detail for logs |
| Status | requested → planning → waiting_for_source → processing → verifying → completed | pending → processing → completed/failed/… |

## Manual on-demand request

1. **Request subtitles** (Dashboard / Media / media file page)
2. Search Bazarr media (or use pre-selected candidate)
3. Choose language (dropdown or typed name/code)
4. Backend normalizes language and creates/reuses an active task
5. Task planner checks whether the target already exists → may complete immediately
6. Otherwise creates the next necessary job (request / extract / translate)
7. Worker processes the job; planner continues until verified

## Automatic fallback

The candidate scanner still applies grace period, retry cooldown, and attempt limits.

After those gates:

```text
candidate → upsert MediaItem → ensure LocalizationTask(origin=automatic) → TaskPlanner.plan
```

Manual and automatic share the same orchestrator. Differences are `origin` and priority (`manual` → high → jobs with `trigger_type=manual` are claimed first).

## Language normalization

Backend-authoritative (`GET /api/languages`, `normalize_language()`).

- `pt-PT` and `pt-BR` are distinct
- Bare `pt` is generic Portuguese (not auto-promoted to `pt-PT`)
- Aliases such as “Português de Portugal” normalize to `pt-PT`

## Bazarr media provider

`BazarrMediaProvider` supports search/get for movies and episodes. It is a lightweight identity layer, not a second media-management app.

## Task states

- **completed** only when the target subtitle is present and verification succeeds (disk + Bazarr), not merely when the translation API returns
- **waiting_for_source** when no source is available yet
- **cancel** stops new executions and cancels pending jobs; in-flight provider calls may finish
- **retry** resumes from the failed step without redoing successful work

## Cost

Task cost is the sum of authoritative `ai_usage_records` for the task’s jobs (all attempts). There is no second accounting system.

## APIs

| Method | Path |
| --- | --- |
| GET | `/api/languages` |
| GET | `/api/media/search?q=` |
| GET/POST | `/api/media`, `/api/media/{id}` |
| GET | `/api/media/{id}/localization` |
| GET | `/api/media/{id}/actions` |
| POST | `/api/media/{id}/localization-tasks` |
| GET | `/api/localization-tasks`, `/api/localization-tasks/{id}` |
| POST | `/api/localization-tasks/{id}/retry`, `.../cancel` |

Duplicate active tasks return **409** with `task_id`. Unsupported capability (e.g. audio) returns **422**.

## Future

- v0.4 SourceResolver can replace internal source selection without changing task UX
- `capability=audio` can be implemented later without changing the media/task model
