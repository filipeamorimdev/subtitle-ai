import { createRouter, createWebHistory } from 'vue-router'
import CandidatesView from '../views/CandidatesView.vue'
import JobsView from '../views/JobsView.vue'
import JobDetailView from '../views/JobDetailView.vue'
import SettingsView from '../views/SettingsView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'candidates', component: CandidatesView },
    { path: '/jobs', name: 'jobs', component: JobsView },
    { path: '/jobs/:id', name: 'job-detail', component: JobDetailView, props: true },
    { path: '/settings', name: 'settings', component: SettingsView },
  ],
})

export default router
