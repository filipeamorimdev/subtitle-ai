<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '../stores/app'
import type { Candidate } from '../types'

const store = useAppStore()
const router = useRouter()
const translatingKey = ref<string | null>(null)
const extractingKey = ref<string | null>(null)
const actionError = ref<string | null>(null)

onMounted(() => {
  store.loadCandidates().catch(() => undefined)
})

async function refresh() {
  actionError.value = null
  try {
    await store.loadCandidates()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  }
}

async function translate(key: string) {
  translatingKey.value = key
  actionError.value = null
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

function extractLabel(item: Candidate) {
  if (item.active_extract_job_id) return 'Extracting…'
  if (extractingKey.value === item.key) return 'Starting…'
  return 'Extract'
}

function canShowExtract(item: Candidate) {
  return item.can_extract || item.active_extract_job_id != null
}
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="font-display text-3xl font-bold text-ink-900 dark:text-ink-50">Candidates</h1>
        <p class="mt-1 max-w-2xl text-ink-600 dark:text-ink-300">
          Movies and episodes missing your target subtitle. Refresh to query Bazarr. Extract embedded
          text tracks when needed, then Translate.
        </p>
      </div>
      <button
        class="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent/90 disabled:opacity-60"
        type="button"
        :disabled="store.loading"
        @click="refresh"
      >
        {{ store.loading ? 'Refreshing…' : 'Refresh' }}
      </button>
    </div>

    <p v-if="actionError || store.error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
      {{ actionError || store.error }}
    </p>

    <div class="overflow-hidden rounded-xl border border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60">
      <table class="min-w-full text-left text-sm">
        <thead class="border-b border-ink-200 bg-ink-50/80 text-ink-500 dark:border-ink-800 dark:bg-ink-950/50 dark:text-ink-300">
          <tr>
            <th class="px-4 py-3 font-medium">Title</th>
            <th class="px-4 py-3 font-medium">Type</th>
            <th class="px-4 py-3 font-medium">Target</th>
            <th class="px-4 py-3 font-medium">Source</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!store.candidates.length">
            <td class="px-4 py-8 text-ink-500" colspan="6">
              No candidates yet. Configure Bazarr in Settings, then refresh.
            </td>
          </tr>
          <tr
            v-for="item in store.candidates"
            :key="item.key"
            class="border-t border-ink-100 dark:border-ink-800"
          >
            <td class="px-4 py-3 align-top">
              <div class="font-medium text-ink-900 dark:text-ink-50">{{ item.title }}</div>
              <div class="mt-0.5 truncate text-xs text-ink-500">{{ item.media_path }}</div>
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
              <span v-else class="text-ink-500">{{ item.reason || item.reason_code || 'Unavailable' }}</span>
            </td>
            <td class="px-4 py-3 align-top">
              <div class="flex flex-wrap items-center justify-end gap-2">
                <button
                  v-if="canShowExtract(item)"
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  :disabled="!item.can_extract || extractingKey === item.key || item.active_extract_job_id != null"
                  @click="extract(item)"
                >
                  {{ extractLabel(item) }}
                </button>
                <button
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  :disabled="!item.can_translate || translatingKey === item.key"
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
  </section>
</template>
