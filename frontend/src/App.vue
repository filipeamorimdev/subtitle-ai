<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
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
  { to: '/tasks', label: 'Tasks' },
  { to: '/candidates', label: 'Candidates' },
  { to: '/ai', label: 'AI' },
  { to: '/glossaries', label: 'Glossary' },
  { to: '/settings', label: 'Settings' },
]
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
              Media-centric subtitle localization
            </p>
          </div>

          <div class="flex shrink-0 items-center gap-1">
            <nav class="hidden items-center gap-1 text-sm font-medium md:flex">
              <RouterLink
                v-for="link in links"
                :key="`desk-${link.to}`"
                class="rounded-md px-3 py-2 text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
                active-class="bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white"
                :to="link.to"
              >
                {{ link.label }}
              </RouterLink>
            </nav>
            <button
              class="rounded-md px-3 py-2 text-sm font-medium text-ink-600 hover:bg-ink-100 md:hidden dark:text-ink-200 dark:hover:bg-ink-800"
              type="button"
              :aria-expanded="navOpen"
              aria-controls="primary-nav"
              @click="navOpen = !navOpen"
            >
              {{ navOpen ? 'Close' : 'Menu' }}
            </button>
          </div>
        </div>

        <nav
          v-show="navOpen"
          id="primary-nav"
          class="mt-3 flex flex-col gap-1 border-t border-ink-200 pt-3 md:hidden dark:border-ink-800"
        >
          <RouterLink
            v-for="link in links"
            :key="`mob-${link.to}`"
            class="rounded-md px-3 py-2.5 text-sm font-medium text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
            active-class="bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white"
            :to="link.to"
          >
            {{ link.label }}
          </RouterLink>
        </nav>
      </div>
    </header>
    <main class="mx-auto max-w-6xl px-4 py-6 sm:py-8">
      <RouterView />
    </main>
  </div>
</template>
