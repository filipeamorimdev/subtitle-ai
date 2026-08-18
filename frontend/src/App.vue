<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { useAppStore } from './stores/app'

const store = useAppStore()
const route = useRoute()

onMounted(() => {
  store.loadSettings().catch(() => undefined)
})

const links = [
  { to: '/media', label: 'Media' },
]

function linkActive(to: string) {
  const path = route.path
  if (to === '/media') {
    return path.startsWith('/media') || path.startsWith('/tasks') || path.startsWith('/jobs')
  }
  return path === to || path.startsWith(`${to}/`)
}

const settingsActive = computed(() => route.path.startsWith('/settings'))

const linkClass = computed(
  () =>
    'rounded-md px-3 py-2 text-sm font-medium text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800',
)
const iconLinkClass = computed(
  () =>
    'inline-flex items-center justify-center rounded-md p-2 text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800',
)
const activeLinkClass = 'bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white'
</script>

<template>
  <div class="min-h-screen">
    <header class="border-b border-ink-200/80 bg-white/70 backdrop-blur dark:border-ink-800 dark:bg-ink-900/70">
      <div class="mx-auto max-w-6xl px-4 py-3 sm:py-4">
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <RouterLink
              class="font-display text-xl font-bold tracking-tight text-ink-900 hover:text-accent sm:text-2xl dark:text-ink-50 dark:hover:text-accent"
              to="/"
            >
              Subtitle AI
            </RouterLink>
            <p class="hidden text-sm text-ink-500 sm:block dark:text-ink-300">
              Your library, in your language
            </p>
          </div>

          <div class="flex shrink-0 items-center gap-1">
            <nav class="flex items-center gap-1 text-sm font-medium">
              <RouterLink
                v-for="link in links"
                :key="link.to"
                :to="link.to"
                :class="[linkClass, linkActive(link.to) ? activeLinkClass : '']"
              >
                {{ link.label }}
              </RouterLink>
            </nav>
            <RouterLink
              to="/settings"
              title="Settings"
              aria-label="Settings"
              :class="[iconLinkClass, settingsActive ? activeLinkClass : '']"
            >
              <svg
                class="h-5 w-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <circle cx="12" cy="12" r="3" />
                <path
                  d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"
                />
              </svg>
            </RouterLink>
          </div>
        </div>
      </div>
    </header>
    <main class="mx-auto max-w-6xl px-4 py-6 sm:py-8">
      <RouterView />
    </main>
  </div>
</template>
