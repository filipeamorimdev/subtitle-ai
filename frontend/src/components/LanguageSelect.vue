<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { LanguageCatalogItem } from '../types'
import { flagEmoji, flagFromLanguageCode } from '../utils/languages'

const props = defineProps<{
  modelValue: string
  languages: LanguageCatalogItem[]
  placeholder?: string
  searchPlaceholder?: string
  loading?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const open = ref(false)
const query = ref('')
const rootEl = ref<HTMLElement | null>(null)
const searchEl = ref<HTMLInputElement | null>(null)
const listEl = ref<HTMLElement | null>(null)
const activeIndex = ref(0)

function flagFor(lang: Pick<LanguageCatalogItem, 'code' | 'region' | 'flag'>): string {
  if (lang.flag) return lang.flag
  if (lang.region) return flagEmoji(lang.region)
  return flagFromLanguageCode(lang.code)
}

const selectedLanguage = computed(() => {
  const match = props.languages.find((lang) => lang.code === props.modelValue)
  if (match) return match
  if (!props.modelValue) return null
  return {
    code: props.modelValue,
    display_name: props.modelValue,
    aliases: [],
    region: null,
    flag: flagFromLanguageCode(props.modelValue),
  } satisfies LanguageCatalogItem
})

const filteredLanguages = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return props.languages
  return props.languages.filter((lang) => {
    if (lang.display_name.toLowerCase().includes(q)) return true
    if (lang.code.toLowerCase().includes(q)) return true
    if ((lang.region || '').toLowerCase().includes(q)) return true
    return lang.aliases.some((alias) => alias.toLowerCase().includes(q))
  })
})

watch(filteredLanguages, () => {
  activeIndex.value = 0
})

watch(open, async (isOpen) => {
  if (!isOpen) return
  query.value = ''
  activeIndex.value = Math.max(
    0,
    filteredLanguages.value.findIndex((lang) => lang.code === props.modelValue),
  )
  await nextTick()
  searchEl.value?.focus()
  scrollActiveIntoView()
})

function selectLanguage(lang: LanguageCatalogItem) {
  emit('update:modelValue', lang.code)
  open.value = false
}

function toggle() {
  open.value = !open.value
}

function onDocumentClick(event: MouseEvent) {
  if (!open.value || !rootEl.value) return
  if (!rootEl.value.contains(event.target as Node)) {
    open.value = false
  }
}

function scrollActiveIntoView() {
  const list = listEl.value
  if (!list) return
  const item = list.querySelector<HTMLElement>(`[data-index="${activeIndex.value}"]`)
  item?.scrollIntoView({ block: 'nearest' })
}

function onSearchKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (!filteredLanguages.value.length) return
    activeIndex.value = (activeIndex.value + 1) % filteredLanguages.value.length
    scrollActiveIntoView()
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (!filteredLanguages.value.length) return
    activeIndex.value =
      (activeIndex.value - 1 + filteredLanguages.value.length) % filteredLanguages.value.length
    scrollActiveIntoView()
  } else if (event.key === 'Enter') {
    event.preventDefault()
    const lang = filteredLanguages.value[activeIndex.value]
    if (lang) selectLanguage(lang)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    open.value = false
  }
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentClick)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', onDocumentClick)
})
</script>

<template>
  <div ref="rootEl" class="relative min-w-0">
    <button
      type="button"
      class="mt-1 flex w-full min-w-0 items-center justify-between gap-3 rounded-md border border-ink-300 bg-transparent px-3 py-2 text-left dark:border-ink-600"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
    >
      <span class="flex min-w-0 flex-1 items-center gap-3">
        <span class="shrink-0 text-xl leading-none" aria-hidden="true">
          {{ selectedLanguage ? flagFor(selectedLanguage) : '🏳️' }}
        </span>
        <span class="min-w-0 flex-1">
          <span class="block truncate font-medium">
            {{ selectedLanguage?.display_name || placeholder || 'Select a language' }}
          </span>
          <span class="mt-0.5 block truncate text-xs text-ink-500">
            <template v-if="selectedLanguage">{{ selectedLanguage.code }}</template>
            <template v-else>{{ placeholder || 'Search by country, language, or code' }}</template>
          </span>
        </span>
      </span>
      <span class="shrink-0 text-ink-500" aria-hidden="true">▾</span>
    </button>

    <div
      v-if="open"
      class="absolute z-30 mt-1 w-full overflow-hidden rounded-md border border-ink-300 bg-white shadow-lg dark:border-ink-600 dark:bg-ink-900"
    >
      <div class="border-b border-ink-200 p-2 dark:border-ink-700">
        <input
          ref="searchEl"
          v-model="query"
          type="search"
          class="w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 text-sm dark:border-ink-600"
          :placeholder="searchPlaceholder || 'Search countries or languages…'"
          @keydown="onSearchKeydown"
        />
      </div>

      <div v-if="loading" class="px-3 py-4 text-sm text-ink-500">Loading languages…</div>
      <div v-else-if="error" class="px-3 py-4 text-sm text-red-700 dark:text-red-300">{{ error }}</div>
      <div v-else-if="!filteredLanguages.length" class="px-3 py-4 text-sm text-ink-500">
        No languages match “{{ query }}”.
      </div>
      <ul
        v-else
        ref="listEl"
        class="max-h-72 overflow-y-auto py-1"
        role="listbox"
      >
        <li
          v-for="(lang, index) in filteredLanguages"
          :key="lang.code"
          :data-index="index"
          role="option"
          :aria-selected="lang.code === modelValue"
          class="cursor-pointer px-3 py-2 hover:bg-ink-100 dark:hover:bg-ink-800"
          :class="{
            'bg-ink-100 dark:bg-ink-800': index === activeIndex,
            'ring-1 ring-inset ring-accent/40': lang.code === modelValue,
          }"
          @mouseenter="activeIndex = index"
          @click="selectLanguage(lang)"
        >
          <div class="flex items-center gap-3">
            <span class="shrink-0 text-xl leading-none" aria-hidden="true">{{ flagFor(lang) }}</span>
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-medium">{{ lang.display_name }}</div>
              <div class="truncate text-xs text-ink-500">{{ lang.code }}</div>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>
