import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import JobDetailView from '../views/JobDetailView.vue'
import JobStatsView from '../views/JobStatsView.vue'
import MediaDetailView from '../views/MediaDetailView.vue'
import MediaView from '../views/MediaView.vue'
import TranslationsView from '../views/TranslationsView.vue'
import SettingsLanguageView from '../views/SettingsLanguageView.vue'
import SettingsLayout from '../views/SettingsLayout.vue'
import SettingsProvidersView from '../views/SettingsProvidersView.vue'
import SettingsView from '../views/SettingsView.vue'
import TaskRedirectView from '../views/TaskRedirectView.vue'
import AiLayout from '../views/ai/AiLayout.vue'
import AiOverviewView from '../views/ai/AiOverviewView.vue'
import AiModelsView from '../views/ai/AiModelsView.vue'
import AiUsageView from '../views/ai/AiUsageView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: DashboardView },
    { path: '/translations', name: 'translations', component: TranslationsView },
    { path: '/media', name: 'media', component: MediaView },
    { path: '/media/:id', name: 'media-detail', component: MediaDetailView, props: true },
    { path: '/tasks', redirect: '/media' },
    { path: '/tasks/:id', name: 'task-detail', component: TaskRedirectView, props: true },
    {
      path: '/candidates',
      redirect: (to) => ({
        path: '/media',
        query: {
          filter: to.query.filter === 'target-exists' ? 'completed' : 'needs-work',
        },
      }),
    },
    { path: '/jobs', redirect: '/media' },
    { path: '/jobs/:id', name: 'job-detail', component: JobDetailView, props: true },
    { path: '/jobs/:id/stats', name: 'job-stats', component: JobStatsView, props: true },
    {
      path: '/settings',
      component: SettingsLayout,
      redirect: '/settings/general',
      children: [
        { path: 'general', name: 'settings', component: SettingsView },
        { path: 'providers', name: 'settings-providers', component: SettingsProvidersView },
        { path: 'ai-providers', redirect: '/settings/providers' },
        { path: 'models', name: 'settings-models', component: AiModelsView },
        { path: 'language', name: 'settings-language', component: SettingsLanguageView },
        { path: 'models/providers', redirect: '/settings/providers' },
        { path: 'models/routing', redirect: '/settings/models' },
      ],
    },
    { path: '/ai/providers', redirect: '/settings/providers' },
    { path: '/ai/models', redirect: '/settings/models' },
    {
      path: '/ai',
      component: AiLayout,
      redirect: '/ai/overview',
      children: [
        { path: 'overview', name: 'ai-overview', component: AiOverviewView },
        { path: 'usage', name: 'ai-usage', component: AiUsageView },
      ],
    },
  ],
})

export default router
