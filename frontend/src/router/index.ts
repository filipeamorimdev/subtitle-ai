import { createRouter, createWebHistory } from 'vue-router'
import CandidatesView from '../views/CandidatesView.vue'
import DashboardView from '../views/DashboardView.vue'
import GlossariesView from '../views/GlossariesView.vue'
import JobsView from '../views/JobsView.vue'
import JobDetailView from '../views/JobDetailView.vue'
import JobStatsView from '../views/JobStatsView.vue'
import SettingsView from '../views/SettingsView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: DashboardView },
    { path: '/candidates', name: 'candidates', component: CandidatesView },
    { path: '/jobs', name: 'jobs', component: JobsView },
    { path: '/jobs/:id', name: 'job-detail', component: JobDetailView, props: true },
    { path: '/jobs/:id/stats', name: 'job-stats', component: JobStatsView, props: true },
    { path: '/glossaries', name: 'glossaries', component: GlossariesView },
    { path: '/settings', name: 'settings', component: SettingsView },
  ],
})

export default router
