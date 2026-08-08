<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useAppStore } from './stores/app'

const store = useAppStore()

onMounted(() => {
  store.initTheme()
  store.loadSettings().catch(() => undefined)
  store.loadJobs().catch(() => undefined)
})
</script>

<template>
  <div class="min-h-screen">
    <header class="border-b border-ink-200/80 bg-white/70 backdrop-blur dark:border-ink-800 dark:bg-ink-900/70">
      <div class="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-4">
        <div>
          <p class="font-display text-2xl font-bold tracking-tight text-ink-900 dark:text-ink-50">
            Subtitle AI
          </p>
          <p class="text-sm text-ink-500 dark:text-ink-300">
            Translate missing Bazarr subtitles with OpenRouter
          </p>
        </div>
        <nav class="flex items-center gap-1 text-sm font-medium">
          <RouterLink
            class="rounded-md px-3 py-2 text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
            active-class="bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white"
            to="/"
          >
            Candidates
          </RouterLink>
          <RouterLink
            class="rounded-md px-3 py-2 text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
            active-class="bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white"
            to="/jobs"
          >
            Jobs
          </RouterLink>
          <RouterLink
            class="rounded-md px-3 py-2 text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
            active-class="bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white"
            to="/settings"
          >
            Settings
          </RouterLink>
          <button
            class="ml-2 rounded-md px-3 py-2 text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
            type="button"
            @click="store.toggleTheme()"
          >
            {{ store.dark ? 'Light' : 'Dark' }}
          </button>
        </nav>
      </div>
    </header>
    <main class="mx-auto max-w-6xl px-4 py-8">
      <RouterView />
    </main>
  </div>
</template>
