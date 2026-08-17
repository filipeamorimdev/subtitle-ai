import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import GlossariesView from '../views/GlossariesView.vue'
import JobDetailView from '../views/JobDetailView.vue'
import JobStatsView from '../views/JobStatsView.vue'
import MediaDetailView from '../views/MediaDetailView.vue'
import MediaView from '../views/MediaView.vue'
import SettingsAiView from '../views/SettingsAiView.vue'
import SettingsLayout from '../views/SettingsLayout.vue'
import SettingsView from '../views/SettingsView.vue'
import TaskRedirectView from '../views/TaskRedirectView.vue'
import AiLayout from '../views/ai/AiLayout.vue'
import AiOverviewView from '../views/ai/AiOverviewView.vue'
import AiProvidersView from '../views/ai/AiProvidersView.vue'
import AiModelsView from '../views/ai/AiModelsView.vue'
import AiUsageView from '../views/ai/AiUsageView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: DashboardView },
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
    { path: '/glossaries', redirect: '/settings/glossary' },
    {
      path: '/settings',
      component: SettingsLayout,
      redirect: '/settings/general',
      children: [
        { path: 'general', name: 'settings', component: SettingsView },
        {
          path: 'models',
          component: SettingsAiView,
          redirect: '/settings/models/providers',
          children: [
            { path: 'providers', name: 'settings-providers', component: AiProvidersView },
            { path: 'routing', name: 'settings-models', component: AiModelsView },
          ],
        },
        { path: 'glossary', name: 'settings-glossary', component: GlossariesView },
      ],
    },
    { path: '/ai/providers', redirect: '/settings/models/providers' },
    { path: '/ai/models', redirect: '/settings/models/routing' },
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
