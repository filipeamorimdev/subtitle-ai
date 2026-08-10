<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '../stores/app'
import type { BatchJobsResult, Candidate } from '../types'

const store = useAppStore()
const router = useRouter()
const translatingKey = ref<string | null>(null)
const extractingKey = ref<string | null>(null)
const requestingKey = ref<string | null>(null)
const batchBusy = ref<'request' | 'extract' | 'translate' | null>(null)
const actionError = ref<string | null>(null)
const actionInfo = ref<string | null>(null)

const sourceLabel = computed(() => {
  const code = store.settings?.source_languages?.[0] || 'en'
  return code.split('-')[0].toUpperCase()
})

function isTargetDone(item: Candidate) {
  return item.reason_code === 'target_exists'
}

const openCandidates = computed(() => store.candidates.filter((item) => !isTargetDone(item)))

const doneCandidates = computed(() => store.candidates.filter((item) => isTargetDone(item)))

const requestableCount = computed(
  () => openCandidates.value.filter((item) => canRequestSource(item) && !item.active_request_job_id).length,
)

const extractableCount = computed(
  () => openCandidates.value.filter((item) => item.can_extract && !item.active_extract_job_id).length,
)

const translatableCount = computed(
  () => openCandidates.value.filter((item) => item.can_translate).length,
)

onMounted(() => {
  store.loadSettings().catch(() => undefined)
  store.loadCandidates().catch(() => undefined)
})

function summarizeBatch(result: BatchJobsResult, action: string) {
  const parts = [`${action}: queued ${result.created_count}`]
  if (result.reused_count) parts.push(`reused ${result.reused_count}`)
  if (result.skipped_count) parts.push(`skipped ${result.skipped_count}`)
  if (result.errors.length) parts.push(`${result.errors.length} error(s)`)
  return parts.join(', ')
}

async function refresh() {
  actionError.value = null
  actionInfo.value = null
  try {
    await store.loadCandidates()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  }
}

async function requestAllMissing() {
  batchBusy.value = 'request'
  actionError.value = null
  actionInfo.value = null
  try {
    const result = await store.batchRequestSubtitles()
    actionInfo.value = summarizeBatch(result, `Request ${sourceLabel.value}`)
    if (result.errors.length) {
      actionError.value = result.errors.slice(0, 5).join(' · ')
    }
    if (result.created_count + result.reused_count > 0) {
      await router.push('/jobs')
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    batchBusy.value = null
  }
}

async function extractAll() {
  batchBusy.value = 'extract'
  actionError.value = null
  actionInfo.value = null
  try {
    const result = await store.batchExtract()
    actionInfo.value = summarizeBatch(result, 'Extract')
    if (result.errors.length) {
      actionError.value = result.errors.slice(0, 5).join(' · ')
    }
    if (result.created_count + result.reused_count > 0) {
      await router.push('/jobs')
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    batchBusy.value = null
  }
}

async function translateAll() {
  batchBusy.value = 'translate'
  actionError.value = null
  actionInfo.value = null
  try {
    const result = await store.batchTranslate()
    actionInfo.value = summarizeBatch(result, 'Translate')
    if (result.errors.length) {
      actionError.value = result.errors.slice(0, 5).join(' · ')
    }
    if (result.created_count + result.reused_count > 0) {
      await router.push('/jobs')
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    batchBusy.value = null
  }
}

async function translate(key: string) {
  translatingKey.value = key
  actionError.value = null
  actionInfo.value = null
  try {
    const job = await store.translateCandidate(key)
    await router.push(`/jobs/${job.id}`)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    translatingKey.value = null
  }
}

async function extract(item: Candidate) {
  extractingKey.value = item.key
  actionError.value = null
  actionInfo.value = null
  try {
    const job = await store.extractCandidate(item.key)
    // Background job — open detail so progress is visible, then refresh list when done later.
    await router.push(`/jobs/${job.id}`)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    extractingKey.value = null
  }
}

async function requestSource(item: Candidate) {
  requestingKey.value = item.key
  actionError.value = null
  actionInfo.value = null
  try {
    const job = await store.requestSubtitle(item.key)
    await router.push(`/jobs/${job.id}`)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    requestingKey.value = null
  }
}

function viewLogs(item: Candidate) {
  if (item.latest_job_id == null) return
  router.push(`/jobs/${item.latest_job_id}`)
}

function extractLabel(item: Candidate) {
  if (item.active_extract_job_id) return 'Extracting…'
  if (extractingKey.value === item.key) return 'Starting…'
  return 'Extract'
}

function canShowExtract(item: Candidate) {
  if (isTargetDone(item)) return false
  return item.can_extract || item.active_extract_job_id != null
}

function canRequestSource(item: Candidate) {
  if (isTargetDone(item)) return false
  if (item.active_request_job_id != null) return true
  if (item.source_subtitle_path) return false
  if (item.media_type === 'movie') return item.bazarr_movie_id != null
  return item.bazarr_episode_id != null && item.bazarr_series_id != null
}

function requestLabel(item: Candidate) {
  if (item.active_request_job_id) return 'Searching…'
  if (requestingKey.value === item.key) return 'Starting…'
  return `Request ${sourceLabel.value}`
}

function statusText(item: Candidate) {
  if (item.can_translate) return 'Ready'
  if (isTargetDone(item)) return 'Target already exists'
  return item.reason || item.reason_code || 'Unavailable'
}
</script>

<template>
  <section class="space-y-6">
    <div class="space-y-4">
      <div class="min-w-0">
        <h1 class="font-display text-2xl font-bold text-ink-900 sm:text-3xl dark:text-ink-50">Candidates</h1>
        <p class="mt-1 max-w-2xl text-sm text-ink-600 sm:text-base dark:text-ink-300">
          Movies and episodes missing your target subtitle. Refresh to query Bazarr. Request a source
          language from Bazarr, extract embedded text tracks when needed, then Translate.
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
          type="button"
          :disabled="store.loading || batchBusy != null || requestableCount === 0"
          @click="requestAllMissing"
        >
          {{
            batchBusy === 'request'
              ? `Requesting ${sourceLabel}…`
              : `Request all ${sourceLabel}`
          }}
        </button>
        <button
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
          type="button"
          :disabled="store.loading || batchBusy != null || extractableCount === 0"
          @click="extractAll"
        >
          {{ batchBusy === 'extract' ? 'Queuing…' : 'Extract all' }}
        </button>
        <button
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
          type="button"
          :disabled="store.loading || batchBusy != null || translatableCount === 0"
          @click="translateAll"
        >
          {{ batchBusy === 'translate' ? 'Queuing…' : 'Translate all' }}
        </button>
        <button
          class="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent/90 disabled:opacity-60"
          type="button"
          :disabled="store.loading || batchBusy != null"
          @click="refresh"
        >
          {{ store.loading ? 'Refreshing…' : 'Refresh' }}
        </button>
      </div>
    </div>

    <p v-if="actionError || store.error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
      {{ actionError || store.error }}
    </p>
    <p v-else-if="actionInfo" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
      {{ actionInfo }}
    </p>

    <!-- Mobile / tablet card list -->
    <div class="space-y-3 lg:hidden">
      <p v-if="!openCandidates.length" class="rounded-xl border border-ink-200 bg-white/80 px-4 py-8 text-sm text-ink-500 dark:border-ink-800 dark:bg-ink-900/60">
        {{
          doneCandidates.length
            ? 'No open candidates. Finished items are listed below.'
            : 'No candidates yet. Configure Bazarr in Settings, then refresh.'
        }}
      </p>
      <article
        v-for="item in openCandidates"
        :key="`card-${item.key}`"
        class="rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
      >
        <div class="min-w-0">
          <h2 class="font-medium leading-snug text-ink-900 dark:text-ink-50">{{ item.title }}</h2>
          <p class="mt-1 break-all text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</p>
        </div>

        <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
          <div>
            <dt class="text-ink-500">Type</dt>
            <dd class="capitalize text-ink-800 dark:text-ink-100">{{ item.media_type }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Target</dt>
            <dd class="text-ink-800 dark:text-ink-100">{{ item.target_language }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Source</dt>
            <dd class="text-ink-800 dark:text-ink-100">
              <span v-if="item.source_subtitle_path">{{ item.source_language || 'source' }}</span>
              <span v-else class="text-ink-500">None</span>
            </dd>
          </div>
          <div class="col-span-2">
            <dt class="text-ink-500">Status</dt>
            <dd
              class="break-words"
              :class="item.can_translate ? 'text-emerald-700 dark:text-emerald-300' : 'text-ink-500'"
            >
              {{ statusText(item) }}
            </dd>
          </div>
        </dl>

        <div v-if="item.has_embedded" class="mt-3 flex flex-wrap gap-1.5">
          <span
            v-for="(track, idx) in item.embedded_subtitles"
            :key="`${track.label}-${idx}`"
            class="inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
            :class="
              track.kind === 'text'
                ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200'
                : track.kind === 'image'
                  ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200'
                  : 'border-ink-300 bg-ink-50 text-ink-600 dark:border-ink-700 dark:bg-ink-950/50 dark:text-ink-300'
            "
          >
            Embedded {{ track.label }}
          </span>
        </div>

        <div class="mt-4 flex flex-wrap gap-2">
          <button
            v-if="item.latest_job_id != null"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            @click="viewLogs(item)"
          >
            View logs
          </button>
          <button
            v-if="canRequestSource(item)"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            :disabled="requestingKey === item.key || item.active_request_job_id != null || batchBusy != null"
            @click="requestSource(item)"
          >
            {{ requestLabel(item) }}
          </button>
          <button
            v-if="canShowExtract(item)"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            :disabled="!item.can_extract || extractingKey === item.key || item.active_extract_job_id != null || batchBusy != null"
            @click="extract(item)"
          >
            {{ extractLabel(item) }}
          </button>
          <button
            class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            :disabled="!item.can_translate || translatingKey === item.key || batchBusy != null"
            @click="translate(item.key)"
          >
            {{ translatingKey === item.key ? 'Starting…' : 'Translate' }}
          </button>
        </div>
      </article>
    </div>

    <!-- Desktop table -->
    <div class="hidden overflow-x-auto rounded-xl border border-ink-200 bg-white/80 lg:block dark:border-ink-800 dark:bg-ink-900/60">
      <table class="w-full min-w-[56rem] table-fixed text-left text-sm">
        <colgroup>
          <col class="w-[36%]" />
          <col class="w-[8%]" />
          <col class="w-[8%]" />
          <col class="w-[8%]" />
          <col class="w-[14%]" />
          <col class="w-[26%]" />
        </colgroup>
        <thead class="border-b border-ink-200 bg-ink-50/80 text-ink-500 dark:border-ink-800 dark:bg-ink-950/50 dark:text-ink-300">
          <tr>
            <th class="px-4 py-3 font-medium">Title</th>
            <th class="px-4 py-3 font-medium">Type</th>
            <th class="px-4 py-3 font-medium">Target</th>
            <th class="px-4 py-3 font-medium">Source</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="sticky right-0 bg-ink-50/95 px-4 py-3 font-medium dark:bg-ink-950/95">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!openCandidates.length">
            <td class="px-4 py-8 text-ink-500" colspan="6">
              {{
                doneCandidates.length
                  ? 'No open candidates. Finished items are listed below.'
                  : 'No candidates yet. Configure Bazarr in Settings, then refresh.'
              }}
            </td>
          </tr>
          <tr
            v-for="item in openCandidates"
            :key="item.key"
            class="border-t border-ink-100 dark:border-ink-800"
          >
            <td class="px-4 py-3 align-top">
              <div class="font-medium text-ink-900 dark:text-ink-50">{{ item.title }}</div>
              <div class="mt-0.5 truncate text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</div>
              <div v-if="item.has_embedded" class="mt-2 flex flex-wrap gap-1.5">
                <span
                  v-for="(track, idx) in item.embedded_subtitles"
                  :key="`${track.label}-${idx}`"
                  class="inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
                  :class="
                    track.kind === 'text'
                      ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200'
                      : track.kind === 'image'
                        ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200'
                        : 'border-ink-300 bg-ink-50 text-ink-600 dark:border-ink-700 dark:bg-ink-950/50 dark:text-ink-300'
                  "
                >
                  Embedded {{ track.label }}
                </span>
              </div>
            </td>
            <td class="px-4 py-3 align-top capitalize">{{ item.media_type }}</td>
            <td class="px-4 py-3 align-top">{{ item.target_language }}</td>
            <td class="px-4 py-3 align-top">
              <span v-if="item.source_subtitle_path">
                {{ item.source_language || 'source' }}
              </span>
              <span v-else class="text-ink-500">None</span>
            </td>
            <td class="px-4 py-3 align-top">
              <span v-if="item.can_translate" class="text-emerald-700 dark:text-emerald-300">Ready</span>
              <span v-else class="text-ink-500">{{ statusText(item) }}</span>
            </td>
            <td
              class="sticky right-0 bg-white/95 px-4 py-3 align-top shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.25)] dark:bg-ink-900/95"
            >
              <div class="flex flex-wrap items-center justify-end gap-2">
                <button
                  v-if="item.latest_job_id != null"
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  @click="viewLogs(item)"
                >
                  View logs
                </button>
                <button
                  v-if="canRequestSource(item)"
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  :disabled="requestingKey === item.key || item.active_request_job_id != null || batchBusy != null"
                  @click="requestSource(item)"
                >
                  {{ requestLabel(item) }}
                </button>
                <button
                  v-if="canShowExtract(item)"
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  :disabled="!item.can_extract || extractingKey === item.key || item.active_extract_job_id != null || batchBusy != null"
                  @click="extract(item)"
                >
                  {{ extractLabel(item) }}
                </button>
                <button
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  :disabled="!item.can_translate || translatingKey === item.key || batchBusy != null"
                  @click="translate(item.key)"
                >
                  {{ translatingKey === item.key ? 'Starting…' : 'Translate' }}
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <details
      v-if="doneCandidates.length"
      class="group rounded-xl border border-ink-200 bg-white/60 dark:border-ink-800 dark:bg-ink-900/40"
    >
      <summary
        class="cursor-pointer list-none px-4 py-3 text-sm font-medium text-ink-600 marker:content-none dark:text-ink-300 [&::-webkit-details-marker]:hidden"
      >
        <span class="inline-flex items-center gap-2">
          <span class="text-ink-400 transition group-open:rotate-90" aria-hidden="true">▸</span>
          Target already exists ({{ doneCandidates.length }})
        </span>
      </summary>
      <div class="space-y-3 border-t border-ink-200 p-3 lg:hidden dark:border-ink-800">
        <article
          v-for="item in doneCandidates"
          :key="`done-card-${item.key}`"
          class="rounded-lg border border-ink-100 bg-ink-50/50 p-3 dark:border-ink-800 dark:bg-ink-950/30"
        >
          <div class="min-w-0">
            <h2 class="font-medium leading-snug text-ink-800 dark:text-ink-100">{{ item.title }}</h2>
            <p class="mt-1 break-all text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</p>
          </div>
          <p class="mt-2 text-xs text-ink-500">{{ statusText(item) }}</p>
          <button
            v-if="item.latest_job_id != null"
            class="mt-3 rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            @click="viewLogs(item)"
          >
            View logs
          </button>
        </article>
      </div>
      <div class="hidden border-t border-ink-200 lg:block dark:border-ink-800">
        <table class="w-full min-w-[40rem] table-fixed text-left text-sm">
          <colgroup>
            <col class="w-[52%]" />
            <col class="w-[10%]" />
            <col class="w-[10%]" />
            <col class="w-[16%]" />
            <col class="w-[12%]" />
          </colgroup>
          <thead class="bg-ink-50/60 text-ink-500 dark:bg-ink-950/40 dark:text-ink-400">
            <tr>
              <th class="px-4 py-2 font-medium">Title</th>
              <th class="px-4 py-2 font-medium">Type</th>
              <th class="px-4 py-2 font-medium">Target</th>
              <th class="px-4 py-2 font-medium">Status</th>
              <th class="px-4 py-2 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in doneCandidates"
              :key="`done-${item.key}`"
              class="border-t border-ink-100 dark:border-ink-800"
            >
              <td class="px-4 py-2.5 align-top">
                <div class="font-medium text-ink-800 dark:text-ink-100">{{ item.title }}</div>
                <div class="mt-0.5 truncate text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</div>
              </td>
              <td class="px-4 py-2.5 align-top capitalize text-ink-600 dark:text-ink-300">{{ item.media_type }}</td>
              <td class="px-4 py-2.5 align-top text-ink-600 dark:text-ink-300">{{ item.target_language }}</td>
              <td class="px-4 py-2.5 align-top text-ink-500">{{ statusText(item) }}</td>
              <td class="px-4 py-2.5 align-top">
                <button
                  v-if="item.latest_job_id != null"
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  @click="viewLogs(item)"
                >
                  View logs
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </section>
</template>
