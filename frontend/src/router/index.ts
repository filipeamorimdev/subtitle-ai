import { createRouter, createWebHistory } from 'vue-router'
import CandidatesView from '../views/CandidatesView.vue'
import DashboardView from '../views/DashboardView.vue'
import GlossariesView from '../views/GlossariesView.vue'
import JobsView from '../views/JobsView.vue'
import JobDetailView from '../views/JobDetailView.vue'
import JobStatsView from '../views/JobStatsView.vue'
import MediaDetailView from '../views/MediaDetailView.vue'
import SettingsView from '../views/SettingsView.vue'
import TaskDetailView from '../views/TaskDetailView.vue'
import TasksView from '../views/TasksView.vue'
import AiLayout from '../views/ai/AiLayout.vue'
import AiOverviewView from '../views/ai/AiOverviewView.vue'
import AiProvidersView from '../views/ai/AiProvidersView.vue'
import AiModelsView from '../views/ai/AiModelsView.vue'
import AiUsageView from '../views/ai/AiUsageView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: DashboardView },
    { path: '/tasks', name: 'tasks', component: TasksView },
    { path: '/tasks/:id', name: 'task-detail', component: TaskDetailView, props: true },
    { path: '/media/:id', name: 'media-detail', component: MediaDetailView, props: true },
    { path: '/candidates', name: 'candidates', component: CandidatesView },
    { path: '/jobs', name: 'jobs', component: JobsView },
    { path: '/jobs/:id', name: 'job-detail', component: JobDetailView, props: true },
    { path: '/jobs/:id/stats', name: 'job-stats', component: JobStatsView, props: true },
    { path: '/glossaries', name: 'glossaries', component: GlossariesView },
    {
      path: '/ai',
      component: AiLayout,
      redirect: '/ai/overview',
      children: [
        { path: 'overview', name: 'ai-overview', component: AiOverviewView },
        { path: 'providers', name: 'ai-providers', component: AiProvidersView },
        { path: 'models', name: 'ai-models', component: AiModelsView },
        { path: 'usage', name: 'ai-usage', component: AiUsageView },
      ],
    },
    { path: '/settings', name: 'settings', component: SettingsView },
  ],
})

export default router
