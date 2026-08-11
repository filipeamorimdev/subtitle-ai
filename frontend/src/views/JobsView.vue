<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import { formatDateTime } from '../utils/datetime'

const store = useAppStore()
const router = useRouter()
let timer: number | undefined

type JobStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'skipped' | 'cancelled'

const statusFilter = ref<JobStatus | null>(null)
const retryingId = ref<number | null>(null)
const actionError = ref<string | null>(null)

const statusCards = computed(() => [
  { label: 'Pending', status: 'pending' as const, count: store.stats?.pending ?? 0 },
  { label: 'Processing', status: 'processing' as const, count: store.stats?.processing ?? 0 },
  { label: 'Completed', status: 'completed' as const, count: store.stats?.completed ?? 0 },
  { label: 'Failed', status: 'failed' as const, count: store.stats?.failed ?? 0 },
  { label: 'Skipped', status: 'skipped' as const, count: store.stats?.skipped ?? 0 },
  { label: 'Cancelled', status: 'cancelled' as const, count: store.stats?.cancelled ?? 0 },
])

const filteredJobs = computed(() => store.jobs)

function canRetry(status: string) {
  return status === 'failed' || status === 'skipped'
}

async function reloadJobs() {
  await store.loadJobs(
    statusFilter.value ? { status: statusFilter.value, limit: 1000 } : undefined,
  )
}

async function toggleStatusFilter(status: JobStatus) {
  statusFilter.value = statusFilter.value === status ? null : status
  await reloadJobs()
}

async function retry(jobId: number) {
  if (retryingId.value != null) return
  retryingId.value = jobId
  actionError.value = null
  try {
    const next = await api.retryJob(jobId)
    await router.push(`/jobs/${next.id}`)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    retryingId.value = null
  }
}

onMounted(async () => {
  await reloadJobs()
  timer = window.setInterval(() => {
    reloadJobs().catch(() => undefined)
  }, 3000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <section class="space-y-6">
    <div>
      <h1 class="font-display text-2xl font-bold sm:text-3xl">Jobs</h1>
      <p class="mt-1 text-sm text-ink-600 sm:text-base dark:text-ink-300">
        History of translation and extraction jobs.
      </p>
    </div>

    <div v-if="store.stats" class="grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3 lg:grid-cols-6">
      <button
        v-for="item in statusCards"
        :key="item.status"
        type="button"
        class="rounded-xl border px-3 py-3 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:px-4"
        :class="
          statusFilter === item.status
            ? 'border-accent bg-accent/10 dark:bg-accent/20'
            : 'border-ink-200 bg-white/80 hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60 dark:hover:border-accent/50'
        "
        :aria-pressed="statusFilter === item.status"
        @click="toggleStatusFilter(item.status)"
      >
        <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">{{ item.label }}</div>
        <div class="mt-1 font-display text-xl font-bold sm:text-2xl">{{ item.count }}</div>
      </button>
    </div>

    <p
      v-if="actionError"
      class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
    >
      {{ actionError }}
    </p>

    <!-- Mobile / tablet cards -->
    <div class="space-y-3 lg:hidden">
      <p
        v-if="!filteredJobs.length"
        class="rounded-xl border border-ink-200 bg-white/80 px-4 py-8 text-sm text-ink-500 dark:border-ink-800 dark:bg-ink-900/60"
      >
        {{ statusFilter ? `No ${statusFilter} jobs.` : 'No jobs yet.' }}
      </p>
      <article
        v-for="job in filteredJobs"
        :key="`card-${job.id}`"
        class="rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
      >
        <div class="flex items-start justify-between gap-3">
          <RouterLink
            class="min-w-0 flex-1 font-medium leading-snug text-accent hover:underline"
            :to="`/jobs/${job.id}`"
          >
            {{ job.media_title || job.media_path }}
          </RouterLink>
          <button
            v-if="canRetry(job.status)"
            type="button"
            class="shrink-0 rounded-md p-1.5 text-ink-500 transition hover:bg-ink-100 hover:text-accent disabled:opacity-50 dark:hover:bg-ink-800"
            title="Retry"
            aria-label="Retry"
            :disabled="retryingId === job.id"
            @click="retry(job.id)"
          >
            <svg
              class="h-4 w-4"
              :class="retryingId === job.id ? 'animate-spin' : ''"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M21 12a9 9 0 1 1-2.64-6.36" />
              <path d="M21 3v6h-6" />
            </svg>
          </button>
        </div>
        <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
          <div>
            <dt class="text-ink-500">Kind</dt>
            <dd class="capitalize">{{ job.job_kind || 'translate' }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Target</dt>
            <dd>{{ job.target_language }}</dd>
          </div>
          <div class="col-span-2 min-w-0">
            <dt class="text-ink-500">Model</dt>
            <dd class="truncate" :title="job.model">{{ job.model }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Status</dt>
            <dd class="capitalize">
              {{ job.status }}
              <span v-if="job.status === 'processing'" class="text-ink-500">({{ job.progress }}%)</span>
            </dd>
          </div>
          <div>
            <dt class="text-ink-500">Created</dt>
            <dd class="break-all text-ink-500">{{ formatDateTime(job.created_at) }}</dd>
          </div>
        </dl>
      </article>
    </div>

    <!-- Desktop table -->
    <div class="hidden overflow-x-auto rounded-xl border border-ink-200 bg-white/80 lg:block dark:border-ink-800 dark:bg-ink-900/60">
      <table class="min-w-[48rem] w-full text-left text-sm">
        <thead class="border-b border-ink-200 bg-ink-50/80 text-ink-500 dark:border-ink-800 dark:bg-ink-950/50 dark:text-ink-300">
          <tr>
            <th class="px-4 py-3 font-medium">Media</th>
            <th class="px-4 py-3 font-medium">Kind</th>
            <th class="px-4 py-3 font-medium">Target</th>
            <th class="px-4 py-3 font-medium">Model</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 font-medium">Created</th>
            <th class="w-12 px-4 py-3 font-medium"><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!filteredJobs.length">
            <td class="px-4 py-8 text-ink-500" colspan="7">
              {{ statusFilter ? `No ${statusFilter} jobs.` : 'No jobs yet.' }}
            </td>
          </tr>
          <tr
            v-for="job in filteredJobs"
            :key="job.id"
            class="border-t border-ink-100 dark:border-ink-800"
          >
            <td class="px-4 py-3">
              <RouterLink class="font-medium text-accent hover:underline" :to="`/jobs/${job.id}`">
                {{ job.media_title || job.media_path }}
              </RouterLink>
            </td>
            <td class="px-4 py-3 capitalize">{{ job.job_kind || 'translate' }}</td>
            <td class="px-4 py-3">{{ job.target_language }}</td>
            <td class="px-4 py-3">{{ job.model }}</td>
            <td class="px-4 py-3 capitalize">
              {{ job.status }}
              <span v-if="job.status === 'processing'" class="text-ink-500">
                ({{ job.progress }}%)
              </span>
            </td>
            <td class="px-4 py-3 text-ink-500">{{ formatDateTime(job.created_at) }}</td>
            <td class="px-4 py-3">
              <button
                v-if="canRetry(job.status)"
                type="button"
                class="rounded-md p-1.5 text-ink-500 transition hover:bg-ink-100 hover:text-accent disabled:opacity-50 dark:hover:bg-ink-800"
                title="Retry"
                aria-label="Retry"
                :disabled="retryingId === job.id"
                @click="retry(job.id)"
              >
                <svg
                  class="h-4 w-4"
                  :class="retryingId === job.id ? 'animate-spin' : ''"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  aria-hidden="true"
                >
                  <path d="M21 12a9 9 0 1 1-2.64-6.36" />
                  <path d="M21 3v6h-6" />
                </svg>
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
