import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../services/api'
import type { Candidate, Job, Settings, Stats } from '../types'

export const useAppStore = defineStore('app', () => {
  const settings = ref<Settings | null>(null)
  const candidates = ref<Candidate[]>([])
  const jobs = ref<Job[]>([])
  const stats = ref<Stats | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const dark = ref(false)

  function applyTheme() {
    document.documentElement.classList.toggle('dark', dark.value)
  }

  function toggleTheme() {
    dark.value = !dark.value
    localStorage.setItem('subtitle-ai-theme', dark.value ? 'dark' : 'light')
    applyTheme()
  }

  function initTheme() {
    const saved = localStorage.getItem('subtitle-ai-theme')
    dark.value = saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches)
    applyTheme()
  }

  async function loadSettings() {
    settings.value = await api.getSettings()
  }

  async function loadCandidates() {
    loading.value = true
    error.value = null
    try {
      candidates.value = await api.refreshCandidates()
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function loadJobs() {
    jobs.value = await api.getJobs()
    stats.value = await api.getStats()
  }

  async function translateCandidate(key: string) {
    const job = await api.createJob({ candidate_key: key })
    await loadJobs()
    return job
  }

  async function extractCandidate(key: string) {
    const job = await api.extractCandidate(key)
    await loadJobs()
    return job
  }

  async function requestSubtitle(key: string, language?: string) {
    const job = await api.requestSubtitle(key, language)
    await loadJobs()
    return job
  }

  async function batchRequestSubtitles() {
    const result = await api.batchRequestSubtitles()
    await loadJobs()
    try {
      await loadCandidates()
    } catch {
      /* keep batch result even if refresh fails */
    }
    return result
  }

  async function batchExtract() {
    const result = await api.batchExtract()
    await loadJobs()
    try {
      await loadCandidates()
    } catch {
      /* keep batch result even if refresh fails */
    }
    return result
  }

  async function batchTranslate() {
    const result = await api.batchTranslate()
    await loadJobs()
    try {
      await loadCandidates()
    } catch {
      /* keep batch result even if refresh fails */
    }
    return result
  }

  return {
    settings,
    candidates,
    jobs,
    stats,
    loading,
    error,
    dark,
    initTheme,
    toggleTheme,
    loadSettings,
    loadCandidates,
    loadJobs,
    translateCandidate,
    extractCandidate,
    requestSubtitle,
    batchRequestSubtitles,
    batchExtract,
    batchTranslate,
  }
})
