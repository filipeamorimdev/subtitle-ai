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
  }
})
