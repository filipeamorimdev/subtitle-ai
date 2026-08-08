<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const router = useRouter()
const translatingKey = ref<string | null>(null)
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
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="font-display text-3xl font-bold text-ink-900 dark:text-ink-50">Candidates</h1>
        <p class="mt-1 max-w-2xl text-ink-600 dark:text-ink-300">
          Movies and episodes missing your target subtitle. Refresh to query Bazarr. Translation only
          starts when you click Translate.
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
            <td class="px-4 py-3">
              <div class="font-medium text-ink-900 dark:text-ink-50">{{ item.title }}</div>
              <div class="mt-0.5 truncate text-xs text-ink-500">{{ item.media_path }}</div>
            </td>
            <td class="px-4 py-3 capitalize">{{ item.media_type }}</td>
            <td class="px-4 py-3">{{ item.target_language }}</td>
            <td class="px-4 py-3">
              <span v-if="item.source_subtitle_path">
                {{ item.source_language || 'source' }}
              </span>
              <span v-else class="text-ink-500">None</span>
            </td>
            <td class="px-4 py-3">
              <span v-if="item.can_translate" class="text-emerald-700 dark:text-emerald-300">Ready</span>
              <span v-else class="text-ink-500">{{ item.reason || item.reason_code || 'Unavailable' }}</span>
            </td>
            <td class="px-4 py-3 text-right">
              <button
                class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                type="button"
                :disabled="!item.can_translate || translatingKey === item.key"
                @click="translate(item.key)"
              >
                {{ translatingKey === item.key ? 'Starting…' : 'Translate' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
