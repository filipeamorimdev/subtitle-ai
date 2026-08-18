import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../services/api'
import type { Candidate, LocalizationTask, MediaItem, Settings } from '../types'

export const useAppStore = defineStore('app', () => {
  const settings = ref<Settings | null>(null)
  const candidates = ref<Candidate[]>([])
  const mediaItems = ref<MediaItem[]>([])
  const localizationTasks = ref<LocalizationTask[]>([])
  const mediaListLoaded = ref(false)
  const loading = ref(false)

  async function loadSettings() {
    settings.value = await api.getSettings()
  }

  async function loadCandidates() {
    loading.value = true
    try {
      candidates.value = await api.refreshCandidates()
    } finally {
      loading.value = false
    }
  }

  /** Load last cached candidate list without hitting Bazarr. */
  async function loadCandidatesCached() {
    candidates.value = await api.getCandidates()
  }

  async function loadMediaList() {
    const [media, taskList] = await Promise.all([
      api.listMedia(500),
      api.getLocalizationTasks({ limit: 500 }),
    ])
    mediaItems.value = media
    localizationTasks.value = taskList
    mediaListLoaded.value = true
  }

  return {
    settings,
    candidates,
    mediaItems,
    localizationTasks,
    mediaListLoaded,
    loading,
    loadSettings,
    loadCandidates,
    loadCandidatesCached,
    loadMediaList,
  }
})
