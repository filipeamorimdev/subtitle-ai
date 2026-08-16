<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { api } from '../services/api'
import type { LocalizationTask } from '../types'
import { formatDateTime } from '../utils/datetime'

const props = defineProps<{ id: string }>()
const router = useRouter()
const task = ref<LocalizationTask | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)
let timer: number | undefined

const taskId = computed(() => Number(props.id))

async function load() {
  if (!Number.isFinite(taskId.value)) {
    error.value = 'Invalid task id'
    return
  }
  try {
    task.value = await api.getLocalizationTask(taskId.value)
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function stepIcon(state: string) {
  if (state === 'done') return '✓'
  if (state === 'active') return '⟳'
  if (state === 'failed') return '✗'
  if (state === 'skipped') return '—'
  return '○'
}

function canCancel(status: string) {
  return ['requested', 'planning', 'waiting_for_source', 'processing', 'verifying'].includes(status)
}

function canRetry(status: string) {
  return ['failed', 'blocked', 'cancelled', 'verifying', 'waiting_for_source'].includes(status)
}

async function cancel() {
  if (!task.value || busy.value) return
  busy.value = true
  try {
    task.value = await api.cancelLocalizationTask(task.value.id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function retry() {
  if (!task.value || busy.value) return
  busy.value = true
  try {
    task.value = await api.retryLocalizationTask(task.value.id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

watch(
  () => props.id,
  () => {
    load().catch(() => undefined)
  },
)

onMounted(async () => {
  await load()
  timer = window.setInterval(() => {
    if (task.value && ['completed', 'cancelled'].includes(task.value.status)) return
    load().catch(() => undefined)
  }, 3000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-center gap-2 text-sm">
      <button type="button" class="text-accent hover:underline" @click="router.push('/tasks')">
        ← Tasks
      </button>
      <span class="text-ink-400">/</span>
      <span class="text-ink-500">Task #{{ id }}</span>
    </div>

    <p v-if="error" class="text-sm text-red-600">{{ error }}</p>
    <div v-else-if="!task" class="text-sm text-ink-500">Loading…</div>

    <template v-else>
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 class="font-display text-2xl font-bold sm:text-3xl">
            <RouterLink class="hover:text-accent" :to="`/media/${task.media_item_id}`">
              {{ task.media_title || `Media #${task.media_item_id}` }}
            </RouterLink>
            → {{ task.target_language_code }}
          </h1>
          <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
            {{ task.target_language_name }} ·
            <span class="capitalize">{{ task.capability }}</span>
            ·
            <span class="capitalize">{{ task.origin }}</span>
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            v-if="canRetry(task.status)"
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
            :disabled="busy"
            @click="retry"
          >
            Retry
          </button>
          <button
            v-if="canCancel(task.status)"
            type="button"
            class="rounded-md border border-red-300 px-3 py-1.5 text-sm font-semibold text-red-700 dark:border-red-800 dark:text-red-300"
            :disabled="busy"
            @click="cancel"
          >
            Cancel task
          </button>
        </div>
      </div>

      <div class="rounded-xl border border-ink-200 p-4 dark:border-ink-700">
        <p class="text-sm font-semibold uppercase tracking-wide text-ink-500">Status</p>
        <p class="mt-1 text-lg font-semibold capitalize">
          {{ task.status.replaceAll('_', ' ') }}
          <span v-if="task.substate" class="text-sm font-normal text-ink-500">
            · {{ task.substate.replaceAll('_', ' ') }}
          </span>
        </p>
        <p v-if="task.error_message" class="mt-2 text-sm text-red-600">{{ task.error_message }}</p>
      </div>

      <div class="rounded-xl border border-ink-200 p-4 dark:border-ink-700">
        <p class="text-sm font-semibold uppercase tracking-wide text-ink-500">Progress</p>
        <ul class="mt-3 space-y-2">
          <li
            v-for="step in task.progress_steps"
            :key="step.id"
            class="flex items-center gap-2 text-sm"
          >
            <span class="w-4 text-center font-semibold">{{ stepIcon(step.state) }}</span>
            <span :class="step.state === 'active' ? 'font-semibold' : ''">
              {{ step.label }}
              <span v-if="step.state === 'skipped'" class="text-ink-500"> · Skipped</span>
            </span>
          </li>
        </ul>
      </div>

      <div v-if="task.ai" class="rounded-xl border border-ink-200 p-4 dark:border-ink-700">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-sm font-semibold uppercase tracking-wide text-ink-500">AI</p>
          <RouterLink class="text-sm text-accent hover:underline" to="/ai/usage">
            Open AI Control Center
          </RouterLink>
        </div>
        <dl class="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
          <div>
            <dt class="text-ink-500">Provider</dt>
            <dd class="font-medium">{{ task.ai.provider_id || '—' }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Model</dt>
            <dd class="break-all font-medium">{{ task.ai.model_id || '—' }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Requests</dt>
            <dd class="font-medium">{{ task.ai.requests }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Tokens</dt>
            <dd class="font-medium">{{ task.ai.tokens.toLocaleString() }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Cost</dt>
            <dd class="font-medium">${{ task.ai.cost_usd.toFixed(4) }}</dd>
          </div>
        </dl>
      </div>

      <div class="rounded-xl border border-ink-200 p-4 dark:border-ink-700">
        <p class="text-sm font-semibold uppercase tracking-wide text-ink-500">
          Execution history
        </p>
        <div v-if="!task.executions.length" class="mt-3 text-sm text-ink-500">
          No executions yet.
        </div>
        <ul v-else class="mt-3 divide-y divide-ink-100 dark:divide-ink-800">
          <li
            v-for="job in task.executions"
            :key="job.id"
            class="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
          >
            <div>
              <RouterLink class="font-medium text-accent hover:underline" :to="`/jobs/${job.id}`">
                {{ job.job_kind }} #{{ job.id }}
              </RouterLink>
              <p class="text-xs text-ink-500">{{ formatDateTime(job.created_at) }}</p>
            </div>
            <span
              class="rounded-full bg-ink-100 px-2 py-0.5 text-xs font-semibold capitalize dark:bg-ink-800"
            >
              {{ job.status }}
            </span>
          </li>
        </ul>
      </div>
    </template>
  </section>
</template>
