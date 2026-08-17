<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { useAppStore } from './stores/app'

const store = useAppStore()
const route = useRoute()
const navOpen = ref(false)

onMounted(() => {
  store.loadSettings().catch(() => undefined)
  store.loadJobs().catch(() => undefined)
})

watch(
  () => route.fullPath,
  () => {
    navOpen.value = false
  },
)

const links = [
  { to: '/', label: 'Dashboard' },
  { to: '/media', label: 'Media' },
  { to: '/settings', label: 'Settings' },
]

function linkActive(to: string) {
  const path = route.path
  if (to === '/') return path === '/' || path.startsWith('/ai')
  if (to === '/media') {
    return path.startsWith('/media') || path.startsWith('/tasks') || path.startsWith('/jobs')
  }
  if (to === '/settings') return path.startsWith('/settings')
  return path === to || path.startsWith(`${to}/`)
}

const linkClass = computed(
  () =>
    'rounded-md px-3 py-2 text-sm font-medium text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800',
)
const activeLinkClass =
  'bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white'
</script>

<template>
  <div class="flex min-h-screen">
    <aside
      class="hidden w-60 shrink-0 flex-col border-r border-ink-200/80 bg-white/70 backdrop-blur md:flex dark:border-ink-800 dark:bg-ink-900/70"
    >
      <div class="border-b border-ink-200/80 px-4 py-4 dark:border-ink-800">
        <RouterLink
          class="font-display text-xl font-bold tracking-tight text-ink-900 hover:text-accent dark:text-ink-50 dark:hover:text-accent"
          to="/"
        >
          Subtitle AI
        </RouterLink>
        <p class="mt-1 text-xs text-ink-500 dark:text-ink-300">Media-centric subtitle localization</p>
      </div>
      <nav class="flex flex-1 flex-col gap-1 p-3">
        <RouterLink
          v-for="link in links"
          :key="`side-${link.to}`"
          :to="link.to"
          :class="[linkClass, linkActive(link.to) ? activeLinkClass : '']"
        >
          {{ link.label }}
        </RouterLink>
      </nav>
    </aside>

    <div class="flex min-w-0 flex-1 flex-col">
      <header class="border-b border-ink-200/80 bg-white/70 backdrop-blur md:hidden dark:border-ink-800 dark:bg-ink-900/70">
        <div class="flex items-center justify-between gap-3 px-4 py-3">
          <RouterLink
            class="font-display text-xl font-bold tracking-tight text-ink-900 hover:text-accent dark:text-ink-50 dark:hover:text-accent"
            to="/"
          >
            Subtitle AI
          </RouterLink>
          <button
            class="rounded-md px-3 py-2 text-sm font-medium text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
            type="button"
            :aria-expanded="navOpen"
            aria-controls="primary-nav"
            @click="navOpen = !navOpen"
          >
            {{ navOpen ? 'Close' : 'Menu' }}
          </button>
        </div>
        <nav
          v-show="navOpen"
          id="primary-nav"
          class="flex flex-col gap-1 border-t border-ink-200 px-3 py-3 dark:border-ink-800"
        >
          <RouterLink
            v-for="link in links"
            :key="`mob-${link.to}`"
            :to="link.to"
            :class="[linkClass, linkActive(link.to) ? activeLinkClass : '']"
          >
            {{ link.label }}
          </RouterLink>
        </nav>
      </header>

      <main class="min-w-0 flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <div class="mx-auto max-w-7xl">
          <RouterView />
        </div>
      </main>
    </div>
  </div>
</template>
