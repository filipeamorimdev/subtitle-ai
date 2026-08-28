import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import DubCastView from '../views/DubCastView.vue'
import JobDetailView from '../views/JobDetailView.vue'
import MediaDetailView from '../views/MediaDetailView.vue'
import MediaView from '../views/MediaView.vue'
import SettingsLayout from '../views/SettingsLayout.vue'
import SettingsProvidersView from '../views/SettingsProvidersView.vue'
import SettingsView from '../views/SettingsView.vue'
import TaskRedirectView from '../views/TaskRedirectView.vue'
import AiModelsView from '../views/ai/AiModelsView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: DashboardView },
    {
      path: '/translations',
      redirect: { path: '/media', query: { filter: 'completed' } },
    },
    { path: '/media', name: 'media', component: MediaView },
    { path: '/media/:id', name: 'media-detail', component: MediaDetailView, props: true },
    { path: '/media/:id/dub-cast', name: 'dub-cast', component: DubCastView, props: true },
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
    {
      path: '/jobs/:id/stats',
      redirect: (to) => ({
        path: `/jobs/${to.params.id}`,
        hash: '#usage',
        query: to.query,
      }),
    },
    {
      path: '/settings',
      component: SettingsLayout,
      redirect: '/settings/general',
      children: [
        { path: 'general', name: 'settings', component: SettingsView },
        { path: 'providers', name: 'settings-providers', component: SettingsProvidersView },
        { path: 'ai-providers', redirect: '/settings/providers' },
        { path: 'models', name: 'settings-models', component: AiModelsView },
        { path: 'language', redirect: { path: '/settings/general', hash: '#language' } },
        { path: 'models/providers', redirect: '/settings/providers' },
        { path: 'models/routing', redirect: '/settings/models' },
      ],
    },
    { path: '/ai/providers', redirect: '/settings/providers' },
    { path: '/ai/models', redirect: '/settings/models' },
    { path: '/ai', redirect: { path: '/', query: { tab: 'ai' } } },
    { path: '/ai/overview', redirect: { path: '/', query: { tab: 'ai' } } },
    {
      path: '/ai/usage',
      redirect: { path: '/', query: { tab: 'ai', report: 'usage' } },
    },
  ],
  scrollBehavior(to) {
    if (to.hash) {
      return { el: to.hash, behavior: 'smooth' }
    }
    return { top: 0 }
  },
})

export default router
