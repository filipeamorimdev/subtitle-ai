# Subtitle AI — v0.1 Implementation Handoff

## Purpose

Build **Subtitle AI**, a standalone self-hosted Docker application that acts as an AI subtitle-translation fallback for **Bazarr**.

The primary use case is:

> Bazarr cannot find a subtitle in the user's desired language, but a subtitle in another language (typically English) exists. Subtitle AI should detect this situation, translate the existing subtitle with an LLM through **OpenRouter**, preserve all subtitle timing/formatting/markup, write the translated subtitle beside the media file, and tell Bazarr to rescan.

This document is the implementation specification for an AI coding agent working in Cursor. The agent should implement the complete v0.1 MVP, not merely produce scaffolding.

---

# 1. Product Goal

Create a polished but deliberately small self-hosted application with:

- Docker support.
- A web UI.
- A backend REST API.
- Bazarr integration.
- SRT subtitle support.
- OpenRouter API integration.
- Configurable OpenRouter API key.
- Configurable OpenRouter model.
- Automatic detection of missing target-language subtitles.
- Translation of an existing source subtitle.
- Preservation of subtitle timing and structure.
- Validation of the translated result.
- Writing the translated subtitle to the media directory.
- Bazarr rescan after successful generation.
- Job queue/status handling.
- Job history.
- Manual retry.

Do **not** implement additional AI providers in v0.1.

The only AI gateway/provider is **OpenRouter**.

The architecture should nevertheless keep the OpenRouter client behind a small internal interface so the translation code is not tightly coupled to HTTP implementation details. Do not build provider-specific abstractions or configuration for other services yet.

---

# 2. Core User Experience

The intended user experience is:

1. User runs Subtitle AI in Docker.
2. User opens the web UI.
3. User configures:
   - Bazarr URL.
   - Bazarr API key if required by the installed Bazarr version/API configuration.
   - OpenRouter API key.
   - OpenRouter model.
   - Media path mapping.
   - Target language.
   - Source language(s).
4. Subtitle AI connects to Bazarr and verifies the configuration.
5. Subtitle AI periodically checks for media where the desired subtitle is missing.
6. It determines whether a usable source subtitle exists.
7. If an English subtitle exists and the desired target subtitle does not, it creates a translation job.
8. The worker translates the subtitle through OpenRouter.
9. The system validates the response.
10. The translated subtitle is written next to the media.
11. Subtitle AI asks Bazarr to rescan/reprocess the relevant item.
12. The generated subtitle becomes available to Bazarr/Jellyfin.
13. The job appears as successful in the UI.

The system must never overwrite the original subtitle.

---

# 3. Example

Input:

```text
/media/movies/Example Movie (2026)/
    Example Movie (2026).mkv
    Example Movie (2026).en.srt
```

Target:

```text
pt-PT
```

Output:

```text
/media/movies/Example Movie (2026)/
    Example Movie (2026).mkv
    Example Movie (2026).en.srt
    Example Movie (2026).pt-PT.srt
```

The original English file remains untouched.

---

# 4. Recommended Technology Stack

Use a pragmatic stack suitable for a self-hosted Docker application.

## Backend

Use:

- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy
- SQLite for v0.1
- Alembic for migrations
- httpx for HTTP requests
- APScheduler or an equivalent lightweight scheduler
- asyncio where appropriate

Do not introduce unnecessary infrastructure.

Redis is **not required for v0.1**.

The job queue can initially be implemented using the database and a worker process/thread model.

The design should allow Redis/Celery/RQ or another queue to be introduced later without rewriting the domain logic.

## Frontend

Use:

- Vue 3
- Vite
- TypeScript
- Pinia
- Vue Router
- Tailwind CSS

Keep the UI clean and functional rather than over-designed.

## Container

Provide:

- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- health endpoint
- persistent `/config`
- configurable `/media` mount

The final image should run both the backend and frontend in a simple self-hosted deployment.

---

# 5. Repository Structure

Use a monorepo approximately like:

```text
subtitle-ai/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── integrations/
│   │   │   └── bazarr/
│   │   ├── subtitles/
│   │   │   ├── parsers/
│   │   │   ├── models/
│   │   │   ├── validation/
│   │   │   └── writer/
│   │   ├── translation/
│   │   │   └── openrouter/
│   │   ├── jobs/
│   │   ├── services/
│   │   └── main.py
│   ├── tests/
│   ├── pyproject.toml
│   └── alembic.ini
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── views/
│   │   ├── stores/
│   │   ├── services/
│   │   ├── router/
│   │   └── types/
│   ├── package.json
│   └── vite.config.ts
│
├── docker/
├── docs/
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── README.md
└── LICENSE
```

The exact structure may be adjusted if the implementation remains clean.

---

# 6. Configuration

Settings must be persisted in the application database/configuration layer.

The UI must provide a Settings page.

## Bazarr settings

Required:

```text
Bazarr URL
Bazarr API key (if applicable)
```

Example:

```text
http://bazarr:6767
```

Provide a **Test Connection** button.

The application must display a clear success/failure result.

## OpenRouter settings

Required:

```text
OpenRouter API Key
OpenRouter Model
```

The API key must be treated as a secret.

Never expose the complete API key through the API or UI after saving.

Display something like:

```text
sk-or-v1-••••••••••••1234
```

The user must be able to replace/delete it.

The model should be configurable as a text value, for example:

```text
openai/gpt-5.6
```

Do not hard-code a particular model.

Provide a **Test OpenRouter Connection** button.

Use the OpenRouter API documented for chat/completions or the current appropriate text-generation endpoint.

Do not assume a particular model's capabilities beyond standard text generation.

---

# 7. Target Language Configuration

v0.1 should support configurable target language.

At minimum support:

```text
Portuguese (Portugal) / pt-PT
```

The implementation should not hard-code translation logic specifically to Portuguese.

The target language should be represented as a language code plus human-readable name.

Example:

```json
{
  "code": "pt-PT",
  "name": "Portuguese (Portugal)"
}
```

The UI can expose a small initial list, including:

- pt-PT
- pt-BR
- en
- es
- fr
- de
- it

The actual translation engine should support arbitrary target language names/codes where the selected model supports them.

---

# 8. Source Language Configuration

Allow the user to define preferred source languages.

Default:

```text
en
```

For v0.1, source subtitle discovery should prefer English.

The implementation may support additional source languages through configuration, but do not overcomplicate the UI.

---

# 9. Bazarr Integration

Bazarr is the primary integration.

Do not fork or modify Bazarr.

The application communicates with Bazarr using its HTTP API.

Create a dedicated integration module:

```text
backend/app/integrations/bazarr/
```

It should encapsulate:

- connection testing
- retrieving relevant media/subtitle information
- identifying missing target subtitles
- triggering rescans/searches where supported
- retrieving subtitle-related metadata
- error handling
- API authentication

Do not spread Bazarr-specific HTTP calls throughout the application.

Use a service such as:

```python
BazarrClient
```

with clearly defined methods.

If a desired Bazarr operation is not available through a stable public API, implement the closest reliable operation and document the limitation rather than relying on browser-only/private behavior.

---

# 10. How Detection Should Work

The service must identify candidates where:

```text
target subtitle missing
AND
source subtitle available
```

Example:

```text
Target = pt-PT

Movie:
    movie.mkv
    movie.en.srt
```

Candidate:

```text
YES
```

But:

```text
movie.mkv
movie.en.srt
movie.pt-PT.srt
```

must not be translated again.

The system must be idempotent.

Repeated scans must not create duplicate jobs or overwrite existing generated subtitles.

---

# 11. Source Subtitle Selection

For v0.1:

1. Prefer subtitle files explicitly matching configured source language.
2. Prefer external subtitle files over embedded subtitles.
3. Prefer SRT.
4. Do not attempt to extract embedded PGS/VobSub subtitles in v0.1.
5. Do not OCR image subtitles in v0.1.
6. Ignore existing target-language subtitles.
7. Never use the target subtitle as a source.

Potential source names:

```text
movie.en.srt
movie.eng.srt
movie.en-US.srt
movie.English.srt
```

Implement reasonable language-tag detection.

Do not rely exclusively on filename parsing if Bazarr provides language metadata.

---

# 12. Subtitle Model

Create an internal subtitle representation.

Example:

```python
SubtitleDocument:
    format
    encoding
    blocks
```

Each block should contain:

```python
SubtitleBlock:
    index
    start
    end
    text
    original_text
    metadata
```

The exact model is up to the implementation.

The critical requirement is that timing and structural metadata remain outside the translated text.

---

# 13. SRT Support

SRT is the only mandatory format for v0.1.

Example input:

```text
1
00:00:01,000 --> 00:00:03,000
Hello, how are you?

2
00:00:04,000 --> 00:00:06,000
I'm fine, thank you.
```

The translation process must produce:

```text
1
00:00:01,000 --> 00:00:03,000
Olá, como estás?

2
00:00:04,000 --> 00:00:06,000
Estou bem, obrigado.
```

The following must remain unchanged:

- block numbering
- timestamps
- number of blocks
- ordering
- blank-line separation

---

# 14. Translation Pipeline

The translation pipeline should be:

```text
Source subtitle
      ↓
Parse
      ↓
Validate source
      ↓
Extract translatable text
      ↓
Batch text
      ↓
OpenRouter
      ↓
Parse response
      ↓
Validate translated structure
      ↓
Reconstruct subtitle
      ↓
Validate final subtitle
      ↓
Write output
      ↓
Bazarr rescan
```

Do not send raw subtitle files blindly to the model.

---

# 15. Batch Translation

Do not send an entire long TV episode as one giant prompt.

Implement batching.

A batch should contain a configurable number of subtitle blocks.

Default:

```text
50 blocks
```

Allow configuration later.

Each batch should preserve block IDs so the model can return a deterministic mapping.

Example:

```text
[001]
Hello.

[002]
Where are you going?

[003]
Wait!
```

Expected response:

```text
[001]
Olá.

[002]
Onde vais?

[003]
Espera!
```

The model must never be allowed to modify timestamps or IDs.

---

# 16. OpenRouter Translation Prompt

Create a robust system prompt.

Core requirements:

- Translate only visible subtitle dialogue.
- Preserve meaning.
- Preserve speaker distinctions.
- Do not translate character names unless the glossary says to.
- Do not add explanations.
- Do not omit lines.
- Do not merge lines.
- Do not split lines.
- Do not alter subtitle IDs.
- Do not add markdown.
- Return exactly the requested structure.
- Use natural spoken language.
- Respect the requested target locale.
- For Portuguese Portugal, use European Portuguese rather than Brazilian Portuguese.

Example system instruction:

```text
You are a professional audiovisual subtitle translator.

Translate subtitle dialogue from the source language to the requested target language and locale.

Preserve:
- subtitle block IDs
- block ordering
- one translated result for every input block
- speaker intent
- tone
- profanity level
- names and proper nouns unless translation is clearly appropriate

Do not:
- add explanations
- add commentary
- merge blocks
- split blocks
- invent dialogue
- omit dialogue
- return markdown
- return timestamps
- modify IDs

The target locale is important. If the target is Portuguese (Portugal), use European Portuguese vocabulary, grammar, spelling, and natural conversational usage. Do not produce Brazilian Portuguese.

Return only the requested block mapping.
```

The implementation should make the prompt configurable in code but not expose prompt editing in the v0.1 UI.

---

# 17. Preserve Subtitle Formatting

For SRT, support common inline markup such as:

```text
<i>...</i>
<b>...</b>
<u>...</u>
```

The model should not destroy these tags.

Prefer extracting markup tokens before translation.

Example:

```text
<i>Hello</i>
```

can internally become:

```text
<TAG0>Hello<TAG1>
```

Translate only the human-readable content and restore tags afterward.

Validate that the tag structure before and after translation matches.

---

# 18. Validation

Validation is one of the most important components.

Reject a translation if:

- block count changed
- block IDs changed
- block order changed
- timestamps changed
- a block is missing
- unexpected blocks were added
- required markup was removed
- malformed SRT was produced
- translated text is empty when source text was not empty

On validation failure:

1. Retry the batch once with a stricter correction prompt.
2. If it still fails, mark the job failed.
3. Do not write a partial subtitle file.
4. Keep the original source untouched.
5. Show the error in job history.

---

# 19. Retry Strategy

OpenRouter/API failures should use bounded retries.

Suggested:

```text
attempt 1
wait 2s
attempt 2
wait 5s
attempt 3
fail
```

Respect HTTP rate limits and `Retry-After` when available.

Do not endlessly retry.

Translation validation failures may receive one dedicated correction retry.

---

# 20. Output Filename

For SRT:

```text
movie.en.srt
```

becomes:

```text
movie.pt-PT.srt
```

If the source does not contain a language suffix:

```text
movie.srt
```

write:

```text
movie.pt-PT.srt
```

Never overwrite an existing target subtitle.

If the target already exists, the job should be skipped as already completed.

---

# 21. Generated Subtitle Identification

The application should maintain a database record indicating:

```text
source file
source subtitle
target subtitle
target language
translation provider = OpenRouter
model
job ID
created timestamp
```

Do not add custom metadata into the SRT file itself.

The generated file should remain compatible with standard media servers.

---

# 22. Job System

Create a persistent job model.

Suggested states:

```text
pending
processing
completed
failed
cancelled
skipped
```

A job should contain:

```text
id
media path
media type
source subtitle path
target subtitle path
source language
target language
model
status
progress
error
created_at
started_at
completed_at
```

Progress can be represented as:

```text
0-100
```

For example:

```text
12 / 20 batches
```

---

# 23. Worker

Implement a worker that:

1. Fetches a pending job.
2. Locks it.
3. Changes state to processing.
4. Parses the subtitle.
5. Creates translation batches.
6. Sends each batch to OpenRouter.
7. Validates each response.
8. Reconstructs the complete subtitle.
9. Validates the complete result.
10. Writes the output atomically.
11. Requests Bazarr rescan.
12. Marks the job completed.

Use atomic file writing:

```text
temporary file
      ↓
fsync if appropriate
      ↓
atomic rename
```

Never leave a half-written subtitle as the final output.

---

# 24. Avoid Duplicate Jobs

The service must prevent duplicate jobs.

Use a deterministic job key based on something like:

```text
source subtitle path
source subtitle modification time
target language
```

or a content hash.

A stronger approach is:

```text
SHA256(source subtitle content)
+
target language
+
model
```

Store this information in the database.

If the same source/target combination has already completed, do not translate again unless explicitly forced by the user.

---

# 25. Translation Cache

v0.1 should include a basic cache at job level.

The cache should prevent unnecessary repeat translations of the exact same subtitle.

Use a content hash.

Example:

```text
source_hash
target_language
model
```

If all are identical and a successful translation exists, reuse it.

Do not build a complicated global translation-memory system in v0.1.

---

# 26. Settings UI

Create a settings page with sections:

## Bazarr

```text
URL
API Key
[Test Connection]
```

## OpenRouter

```text
API Key
Model
[Test Connection]
```

## Translation

```text
Target Language
Source Language
Automatic Processing [on/off]
Scan Interval
```

## Media

```text
Container media path
```

The UI should explain that the container path must match the paths returned/used by Bazarr.

---

# 27. Dashboard

Create a simple dashboard showing:

```text
Jobs
-----

Pending       4
Processing    1
Completed   128
Failed        3
```

Recent jobs:

```text
Movie / Episode
Source
Target
Model
Status
Created
```

Clicking a job should show details.

---

# 28. Job Detail Page

Show:

```text
Media
Source subtitle
Target subtitle
Source language
Target language
Model
Status
Progress
Created
Started
Completed
Error
```

Actions:

```text
Retry
Cancel
```

Do not expose the OpenRouter API key.

---

# 29. Manual Job Creation

v0.1 should provide a manual translation action.

The user can select/provide:

```text
Source subtitle file
Target language
```

This is useful for testing without waiting for automatic detection.

The manual action should use the same job pipeline as automatic jobs.

---

# 30. Automatic Processing

Implement a scheduler.

Default:

```text
every 5 minutes
```

The scheduler asks Bazarr for relevant missing subtitles/candidates.

Do not create jobs for everything in the library on every scan.

Use persistent job/detection state and only create new work when necessary.

Provide:

```text
Scan Now
```

in the dashboard.

---

# 31. Bazarr Rescan

After successfully writing:

```text
movie.pt-PT.srt
```

request the appropriate Bazarr rescan/update operation.

The integration should make a best effort.

If the subtitle was successfully written but Bazarr rescan fails:

- The job should not be marked as a translation failure.
- Mark the translation as completed with a warning.
- Show the Bazarr rescan error.
- Allow the user to retry the Bazarr synchronization.

The subtitle file must remain on disk.

---

# 32. Error Handling

Errors should be understandable.

Examples:

```text
Could not connect to Bazarr.
```

```text
OpenRouter authentication failed.
```

```text
OpenRouter model not found.
```

```text
No compatible source subtitle was found.
```

```text
Translation response failed validation.
```

```text
Target subtitle already exists.
```

Do not expose raw stack traces in the UI.

Log detailed technical information server-side.

---

# 33. Logging

Use structured application logging.

Include:

- timestamp
- level
- component
- job ID where applicable
- message

Never log:

- OpenRouter API keys
- Bazarr API keys
- complete subtitle contents by default

Subtitle content may contain sensitive/private material and should not be written to normal logs.

---

# 34. Security

The application is intended for local/self-hosted use.

Still:

- Never expose API keys in responses.
- Never put API keys in URLs.
- Never log API keys.
- Validate filesystem paths.
- Prevent path traversal.
- Only access paths explicitly mounted/configured.
- Do not allow arbitrary filesystem browsing through the API.
- Validate uploaded/manual subtitle paths against allowed media roots.

Authentication for the web UI is not mandatory for v0.1 if the application is clearly documented as a trusted-local-network application.

Design the API so authentication can be added later.

---

# 35. API

Implement REST endpoints approximately:

```text
GET    /api/health

GET    /api/settings
PUT    /api/settings

POST   /api/settings/test/bazarr
POST   /api/settings/test/openrouter

GET    /api/jobs
GET    /api/jobs/{id}
POST   /api/jobs
POST   /api/jobs/{id}/retry
POST   /api/jobs/{id}/cancel

POST   /api/scan

GET    /api/stats
```

Do not expose secrets through `GET /api/settings`.

Return masked values.

---

# 36. Health

Provide:

```text
GET /api/health
```

Example:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

Optionally provide dependency status:

```text
Bazarr: connected
Database: healthy
OpenRouter: configured
```

Do not make the health endpoint fail simply because OpenRouter is temporarily unavailable.

---

# 37. Docker

Provide a complete example:

```yaml
services:
  subtitle-ai:
    build: .
    container_name: subtitle-ai
    restart: unless-stopped
    ports:
      - "6768:6768"
    volumes:
      - ./config:/config
      - /path/to/media:/media
    environment:
      - TZ=Europe/Lisbon
```

The actual image should serve the frontend and API.

Document the required volume mapping carefully.

Important:

Bazarr and Subtitle AI must see the media files using compatible paths.

Example:

```text
Bazarr:
    /media/movies

Subtitle AI:
    /media/movies
```

Avoid designs where Bazarr reports:

```text
/movies/Example/file.mkv
```

but Subtitle AI only has:

```text
/media/file.mkv
```

unless a configurable path mapping exists.

For v0.1, support an explicit path mapping configuration if necessary.

---

# 38. Persistence

Persist all application state under:

```text
/config
```

Example:

```text
/config/
    subtitle-ai.db
    logs/
```

The container must remain stateless outside `/config` and mounted media.

---

# 39. Testing Requirements

Write automated tests.

At minimum:

## Subtitle parser tests

- valid SRT
- multiple blocks
- multiline dialogue
- HTML-style tags
- malformed SRT
- Unicode
- Portuguese characters
- apostrophes
- punctuation

## Validation tests

Test detection of:

- changed IDs
- missing blocks
- extra blocks
- changed timestamps
- empty translation
- malformed output
- removed markup

## Filename tests

Test:

```text
movie.en.srt
movie.eng.srt
movie.srt
movie.en-US.srt
```

and expected target output.

## Translation tests

Mock OpenRouter.

Do not make real API calls in the test suite.

Test:

- successful translation
- API failure
- timeout
- rate limiting
- malformed response
- validation retry

## Job tests

Test:

- creation
- locking
- processing
- completion
- failure
- retry
- duplicate prevention

## Bazarr tests

Mock Bazarr HTTP responses.

Test:

- connection
- candidate retrieval
- rescan
- API errors

---

# 40. OpenRouter Client

Implement a dedicated client:

```python
OpenRouterClient
```

Responsibilities:

- authentication
- request construction
- model selection
- timeout
- retries
- rate-limit handling
- response extraction
- error normalization

The rest of the application should not directly construct OpenRouter HTTP requests.

The client should accept:

```python
api_key
model
```

from application configuration.

Do not hard-code a model.

---

# 41. OpenRouter Cost/Usage

If the OpenRouter response exposes usage information, store basic usage data:

```text
input tokens
output tokens
total tokens
```

Also store model name.

Do not make cost accounting a blocker for v0.1.

If pricing cannot be reliably determined from the API response, do not invent a cost.

The UI can show token usage instead.

---

# 42. AI Translation Reliability

The model should not be trusted blindly.

The application, not the LLM, is responsible for subtitle structure.

The LLM is responsible only for translating text.

This separation is critical.

Conceptually:

```text
Subtitle structure
        │
        │ controlled by application
        ▼
[ID] [TIMING] [MARKUP] [TEXT]
                         │
                         ▼
                    OpenRouter
                         │
                         ▼
                       TEXT
```

The application reconstructs the final subtitle.

---

# 43. PT-PT Quality

The target locale must be explicitly included in every translation request.

For:

```text
Portuguese (Portugal)
```

the prompt must explicitly state:

```text
Use European Portuguese (PT-PT), not Brazilian Portuguese (PT-BR).
```

The translation should favor natural audiovisual dialogue rather than literal word-for-word translation.

Do not attempt to build a large Portuguese dictionary in v0.1.

A future glossary/translation-memory feature can address this.

---

# 44. No Whisper in v0.1

Do not implement speech-to-text.

The source subtitle must already exist.

If no source subtitle exists:

```text
No job
```

or a clearly failed/skipped candidate:

```text
No compatible source subtitle found.
```

Whisper/ASR can be a future feature.

---

# 45. No ASS/SSA in v0.1

Do not partially implement ASS.

Architect the subtitle layer so ASS can be added later.

For v0.1:

```text
SRT = supported
ASS/SSA = unsupported
VTT = unsupported
PGS = unsupported
SUP = unsupported
```

The UI should make this clear.

---

# 46. No Multiple AI Providers in v0.1

Do not implement:

- direct OpenAI integration
- direct Gemini integration
- direct Claude integration
- DeepL
- Ollama
- Azure

The only AI backend is:

```text
OpenRouter
```

The user supplies:

```text
OpenRouter API key
OpenRouter model
```

This is an explicit v0.1 requirement.

---

# 47. No Complicated Plugin System in v0.1

Do not build a plugin marketplace or dynamic plugin loader.

Keep the code modular so future integrations can be added.

The first integration is:

```text
Bazarr
```

The first AI gateway is:

```text
OpenRouter
```

---

# 48. UX Principles

The application should be:

- self-explanatory
- dark/light mode compatible
- responsive
- usable on desktop
- understandable to a non-developer self-hosting user

Avoid unnecessary configuration.

A new user should be able to:

```text
Open app
↓
Configure Bazarr
↓
Configure OpenRouter
↓
Select model
↓
Select target language
↓
Enable automatic processing
↓
Done
```

---

# 49. README

Create a high-quality README covering:

- What Subtitle AI does.
- Why it exists.
- Architecture overview.
- Requirements.
- Docker installation.
- Volume mappings.
- Bazarr configuration.
- OpenRouter configuration.
- Model selection.
- Target language configuration.
- Example workflow.
- Troubleshooting.
- Security notes.
- Current limitations.
- Roadmap.

Include a complete Docker Compose example.

---

# 50. Documentation

Create:

```text
docs/
    architecture.md
    configuration.md
    bazarr-integration.md
    translation-pipeline.md
    development.md
```

The documentation should describe the actual implemented behavior.

Do not document hypothetical features as if they already exist.

---

# 51. Definition of Done

v0.1 is complete when all of the following work:

### Installation

- Docker image builds successfully.
- Docker Compose starts successfully.
- `/config` persists application state.
- `/media` can be mounted.

### Configuration

- Bazarr URL can be configured.
- Bazarr credentials can be configured.
- OpenRouter API key can be configured securely.
- OpenRouter model can be selected/configured.
- Target language can be configured.
- Connections can be tested.

### Detection

- Application can communicate with Bazarr.
- Missing target subtitles can be identified.
- Existing source subtitles can be identified.
- Duplicate work is avoided.

### Translation

- SRT can be parsed.
- Subtitle text can be translated through OpenRouter.
- Batches work.
- The application validates model output.
- Invalid output is retried once.
- Failed output is never written as a final subtitle.

### Output

- Target subtitle is written correctly.
- Original subtitle remains untouched.
- Target filename is correct.
- Existing target subtitle is never overwritten.

### Integration

- Bazarr is asked to rescan after successful output.
- A Bazarr failure after successful translation is reported as a warning rather than destroying the job.

### UI

- Dashboard exists.
- Settings exist.
- Job list exists.
- Job detail exists.
- Manual job creation exists.
- Retry exists.
- Errors are understandable.

### Testing

- Unit tests exist for parsing/validation.
- OpenRouter is mocked.
- Bazarr is mocked.
- Job lifecycle is tested.
- Duplicate prevention is tested.

---

# 52. Implementation Order

Implement in this order:

## Phase 1 — Repository and infrastructure

1. Create repository structure.
2. Set up backend.
3. Set up frontend.
4. Set up SQLite/Alembic.
5. Create Docker build.
6. Create health endpoint.

## Phase 2 — Subtitle engine

1. SRT parser.
2. Internal subtitle model.
3. SRT writer.
4. Filename/language detection.
5. Validation.
6. Tests.

Do this before integrating AI.

## Phase 3 — OpenRouter

1. Configuration model.
2. Secure API-key storage.
3. OpenRouter client.
4. Model configuration.
5. Connection test.
6. Translation service.
7. Batch processing.
8. Validation/retry.
9. Tests with mocked API.

## Phase 4 — Bazarr

1. Bazarr configuration.
2. Bazarr client.
3. Connection test.
4. Candidate detection.
5. Source subtitle discovery.
6. Rescan integration.
7. Mocked integration tests.

## Phase 5 — Jobs

1. Job model.
2. Queue.
3. Worker.
4. Duplicate prevention.
5. Retry handling.
6. Progress tracking.
7. Job history.

## Phase 6 — UI

1. Layout.
2. Dashboard.
3. Settings.
4. Jobs list.
5. Job details.
6. Manual translation.
7. Retry/cancel.
8. Connection-test feedback.

## Phase 7 — Integration testing

Test the complete workflow:

```text
Bazarr
  ↓
Missing PT-PT
  ↓
English SRT found
  ↓
Subtitle AI job
  ↓
OpenRouter
  ↓
Validated PT-PT SRT
  ↓
File written
  ↓
Bazarr rescan
  ↓
Completed
```

## Phase 8 — Documentation and packaging

1. README.
2. Docker Compose.
3. Configuration documentation.
4. Architecture documentation.
5. Troubleshooting.
6. Final tests.
7. Version `0.1.0`.

---

# 53. Important Engineering Principles

## Do not over-engineer v0.1

Avoid:

- Kubernetes.
- Microservices.
- Redis unless genuinely necessary.
- Multiple databases.
- Multiple AI providers.
- Plugin marketplaces.
- Complex authentication.
- Cloud infrastructure.
- External hosted backend.

This is a self-hosted Docker application.

## Keep boundaries clean

The important boundaries are:

```text
Bazarr integration
        ↓
Candidate detection
        ↓
Job system
        ↓
Subtitle processing
        ↓
OpenRouter translation
        ↓
Validation
        ↓
File output
        ↓
Bazarr rescan
```

Each component should have a clear responsibility.

## Prefer interfaces at the domain boundary

For example:

```python
class TranslationService:
    async def translate(...):
        ...
```

with the v0.1 implementation:

```text
OpenRouterTranslationService
```

Do not implement additional providers.

Similarly:

```python
class SubtitleParser
class SubtitleWriter
class SubtitleValidator
```

This keeps the system extensible without prematurely implementing a plugin architecture.

---

# 54. Final Instruction to the Cursor Agent

You are implementing a real v0.1 product, not a prototype mockup.

Work through the implementation phases in order.

Before writing large amounts of code:

1. Inspect the repository.
2. Create a concise implementation plan.
3. Identify any ambiguity that materially blocks implementation.
4. Otherwise make reasonable engineering decisions and proceed without repeatedly asking for confirmation.

After each major phase:

- run tests
- fix failures
- keep documentation aligned with implementation

Do not leave placeholder implementations for core functionality.

Do not fake Bazarr integration.

Do not fake OpenRouter calls.

Do not fake subtitle processing.

Do not silently swallow errors.

When the implementation is complete, verify the complete end-to-end flow using mocked external services and provide:

```text
- what was implemented
- how to run it
- what was tested
- known limitations
- files/areas changed
```

The finished result must be a runnable v0.1 Docker application capable of performing the real workflow:

```text
Bazarr detects missing target subtitle
        ↓
Subtitle AI detects/receives candidate
        ↓
Existing source SRT selected
        ↓
SRT parsed
        ↓
Text batched
        ↓
OpenRouter model translates
        ↓
Response validated
        ↓
Target SRT reconstructed
        ↓
Target SRT written beside media
        ↓
Bazarr rescans
        ↓
Job completed
```

The most important quality requirement is:

> **Never sacrifice subtitle structure for translation. Timing, ordering, IDs, formatting, and the original source file must be preserved.**
