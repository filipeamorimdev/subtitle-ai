import { api } from '../services/api'
import type { Candidate, Job, LocalizationTask, MediaRef } from '../types'

export function mediaHref(mediaId: number) {
  return `/media/${mediaId}`
}

export function safeReturnTo(value: unknown): string | null {
  const raw = Array.isArray(value) ? value[0] : value
  if (typeof raw !== 'string') return null
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.includes('://')) return null
  return raw
}

export function withReturnTo(
  path: string,
  from: string | null | undefined,
  extraQuery: Record<string, string> = {},
) {
  const query = { ...extraQuery }
  if (from) query.from = from
  return Object.keys(query).length ? { path, query } : path
}

export function localizationTaskTitle(
  task: Pick<LocalizationTask, 'media_title' | 'media_item_id' | 'media_year'>,
) {
  const title = task.media_title || `Media #${task.media_item_id}`
  return task.media_year ? `${title} (${task.media_year})` : title
}

export function candidateToMediaRef(item: Candidate): MediaRef {
  const isMovie = item.media_type === 'movie'
  return {
    provider_id: 'bazarr',
    external_id: isMovie
      ? `movie:${item.bazarr_movie_id}`
      : `episode:${item.bazarr_episode_id}`,
    media_type: item.media_type,
    title: item.title,
    year: null,
    season: null,
    episode: null,
    episode_title: null,
    path: item.media_path,
    parent_external_id:
      !isMovie && item.bazarr_series_id != null ? `series:${item.bazarr_series_id}` : null,
    bazarr_movie_id: item.bazarr_movie_id,
    bazarr_series_id: item.bazarr_series_id,
    bazarr_episode_id: item.bazarr_episode_id,
  }
}

export function candidateExternalId(item: Candidate) {
  if (item.media_type === 'movie' && item.bazarr_movie_id != null) {
    return `movie:${item.bazarr_movie_id}`
  }
  if (item.bazarr_episode_id != null) return `episode:${item.bazarr_episode_id}`
  return null
}

export async function mediaHrefForTaskId(taskId: number) {
  const task = await api.getLocalizationTask(taskId)
  return mediaHref(task.media_item_id)
}

export async function ensureMediaFromJob(job: Job) {
  return api.ensureMedia({
    media_type: job.media_type === 'episode' ? 'episode' : 'movie',
    title: job.media_title || 'Untitled',
    path: job.media_path,
    bazarr_movie_id: job.bazarr_movie_id ?? null,
    bazarr_series_id: job.bazarr_series_id ?? null,
    bazarr_episode_id: job.bazarr_episode_id ?? null,
  })
}

export async function mediaHrefForJob(job: Job, fallbackMedia?: MediaRef | null) {
  if (job.task_id) {
    return mediaHrefForTaskId(job.task_id)
  }
  if (fallbackMedia) {
    const media = await api.ensureMedia(fallbackMedia)
    return mediaHref(media.id)
  }
  if (job.bazarr_movie_id != null || job.bazarr_episode_id != null || job.media_path) {
    const media = await ensureMediaFromJob(job)
    return mediaHref(media.id)
  }
  return '/media'
}
