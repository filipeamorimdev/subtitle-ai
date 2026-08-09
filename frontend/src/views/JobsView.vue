<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useAppStore } from '../stores/app'

const store = useAppStore()
let timer: number | undefined

type JobStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'skipped' | 'cancelled'

const statusFilter = ref<JobStatus | null>(null)

const statusCards = computed(() => [
  { label: 'Pending', status: 'pending' as const, count: store.stats?.pending ?? 0 },
  { label: 'Processing', status: 'processing' as const, count: store.stats?.processing ?? 0 },
  { label: 'Completed', status: 'completed' as const, count: store.stats?.completed ?? 0 },
  { label: 'Failed', status: 'failed' as const, count: store.stats?.failed ?? 0 },
  { label: 'Skipped', status: 'skipped' as const, count: store.stats?.skipped ?? 0 },
  { label: 'Cancelled', status: 'cancelled' as const, count: store.stats?.cancelled ?? 0 },
])

const filteredJobs = computed(() => {
  if (!statusFilter.value) return store.jobs
  return store.jobs.filter((job) => job.status === statusFilter.value)
})

function toggleStatusFilter(status: JobStatus) {
  statusFilter.value = statusFilter.value === status ? null : status
}

onMounted(async () => {
  await store.loadJobs()
  timer = window.setInterval(() => {
    store.loadJobs().catch(() => undefined)
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
        <RouterLink class="font-medium leading-snug text-accent hover:underline" :to="`/jobs/${job.id}`">
          {{ job.media_title || job.media_path }}
        </RouterLink>
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
            <dd class="break-all text-ink-500">{{ job.created_at }}</dd>
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
          </tr>
        </thead>
        <tbody>
          <tr v-if="!filteredJobs.length">
            <td class="px-4 py-8 text-ink-500" colspan="6">
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
            <td class="px-4 py-3 text-ink-500">{{ job.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
