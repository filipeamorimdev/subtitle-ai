<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../services/api'
import type { Job } from '../types'

const props = defineProps<{ id: string }>()
const router = useRouter()
const job = ref<Job | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)
let timer: number | undefined

async function load() {
  try {
    job.value = await api.getJob(Number(props.id))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

onMounted(async () => {
  await load()
  timer = window.setInterval(() => {
    if (job.value && ['pending', 'processing'].includes(job.value.status)) {
      load()
    }
  }, 2000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})

async function retry() {
  busy.value = true
  error.value = null
  try {
    const next = await api.retryJob(Number(props.id))
    await router.push(`/jobs/${next.id}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function cancel() {
  busy.value = true
  error.value = null
  try {
    job.value = await api.cancelJob(Number(props.id))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function retrySync() {
  busy.value = true
  error.value = null
  try {
    job.value = await api.retryBazarrSync(Number(props.id))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section v-if="job" class="space-y-6">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 class="font-display text-3xl font-bold">{{ job.media_title || 'Job' }} #{{ job.id }}</h1>
        <p class="mt-1 capitalize text-ink-600 dark:text-ink-300">
          {{ job.job_kind || 'translate' }} · {{ job.status }} · {{ job.progress }}%
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          v-if="['failed', 'cancelled', 'skipped'].includes(job.status)"
          class="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-white"
          type="button"
          :disabled="busy"
          @click="retry"
        >
          Retry
        </button>
        <button
          v-if="['pending', 'processing'].includes(job.status)"
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          type="button"
          :disabled="busy"
          @click="cancel"
        >
          Cancel
        </button>
        <button
          v-if="job.status === 'completed' && job.warning"
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          type="button"
          :disabled="busy"
          @click="retrySync"
        >
          Retry Bazarr sync
        </button>
      </div>
    </div>

    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error }}
    </p>

    <dl class="grid gap-4 rounded-xl border border-ink-200 bg-white/80 p-5 text-sm dark:border-ink-800 dark:bg-ink-900/60 sm:grid-cols-2">
      <div>
        <dt class="text-ink-500">Media</dt>
        <dd class="mt-1 break-all">{{ job.media_path }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Kind</dt>
        <dd class="mt-1 capitalize">{{ job.job_kind || 'translate' }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Type</dt>
        <dd class="mt-1 capitalize">{{ job.media_type }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Source subtitle</dt>
        <dd class="mt-1 break-all">{{ job.source_subtitle_path }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Target subtitle</dt>
        <dd class="mt-1 break-all">{{ job.target_subtitle_path }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Languages</dt>
        <dd class="mt-1">{{ job.source_language }} → {{ job.target_language }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Model</dt>
        <dd class="mt-1">{{ job.model }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Progress</dt>
        <dd class="mt-1">{{ job.progress_detail || `${job.progress}%` }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Tokens</dt>
        <dd class="mt-1">
          {{ job.total_tokens ?? '—' }}
          <span v-if="job.input_tokens != null" class="text-ink-500">
            (in {{ job.input_tokens }} / out {{ job.output_tokens }})
          </span>
        </dd>
      </div>
      <div>
        <dt class="text-ink-500">Created</dt>
        <dd class="mt-1">{{ job.created_at }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Started</dt>
        <dd class="mt-1">{{ job.started_at || '—' }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Completed</dt>
        <dd class="mt-1">{{ job.completed_at || '—' }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Reason</dt>
        <dd class="mt-1">{{ job.reason_code || '—' }}</dd>
      </div>
      <div class="sm:col-span-2" v-if="job.error">
        <dt class="text-ink-500">Error</dt>
        <dd class="mt-1 text-red-700 dark:text-red-300">{{ job.error }}</dd>
      </div>
      <div class="sm:col-span-2" v-if="job.warning">
        <dt class="text-ink-500">Warning</dt>
        <dd class="mt-1 text-amber-700 dark:text-amber-300">{{ job.warning }}</dd>
      </div>
    </dl>
  </section>
  <p v-else class="text-ink-500">Loading job…</p>
</template>
