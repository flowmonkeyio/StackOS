import { ref } from 'vue'
import { defineStore } from 'pinia'

import type {
  SchemaArtifactOut,
  SchemaContextSnapshotOut,
  SchemaDecisionOut,
  SchemaExperimentObservationOut,
  SchemaExperimentOut,
  SchemaLearningOut,
  SchemaMetricSnapshotOut,
  SchemaPageResponseArtifactOut,
  SchemaPageResponseContextSnapshotOut,
  SchemaPageResponseDecisionOut,
  SchemaPageResponseExperimentObservationOut,
  SchemaPageResponseExperimentOut,
  SchemaPageResponseLearningOut,
  SchemaPageResponseMetricSnapshotOut,
  SchemaPageResponseProjectEventOut,
  SchemaProjectEventOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'

export const useProjectDataStore = defineStore('projectData', () => {
  const timeline = ref<SchemaProjectEventOut[]>([])
  const snapshots = ref<SchemaContextSnapshotOut[]>([])
  const learnings = ref<SchemaLearningOut[]>([])
  const experiments = ref<SchemaExperimentOut[]>([])
  const observations = ref<SchemaExperimentObservationOut[]>([])
  const decisions = ref<SchemaDecisionOut[]>([])
  const metrics = ref<SchemaMetricSnapshotOut[]>([])
  const artifacts = ref<SchemaArtifactOut[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function refresh(projectId: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const [
        timelinePage,
        snapshotPage,
        learningPage,
        experimentPage,
        observationPage,
        decisionPage,
        metricPage,
        artifactPage,
      ] = await Promise.all([
        apiFetch<SchemaPageResponseProjectEventOut>(
          `/api/v1/projects/${projectId}/context/timeline?limit=50`,
        ),
        apiFetch<SchemaPageResponseContextSnapshotOut>(
          `/api/v1/projects/${projectId}/context/snapshots?limit=50`,
        ),
        apiFetch<SchemaPageResponseLearningOut>(`/api/v1/projects/${projectId}/learnings?limit=50`),
        apiFetch<SchemaPageResponseExperimentOut>(
          `/api/v1/projects/${projectId}/experiments?limit=50`,
        ),
        apiFetch<SchemaPageResponseExperimentObservationOut>(
          `/api/v1/projects/${projectId}/experiments/observations?limit=50`,
        ),
        apiFetch<SchemaPageResponseDecisionOut>(`/api/v1/projects/${projectId}/decisions?limit=50`),
        apiFetch<SchemaPageResponseMetricSnapshotOut>(`/api/v1/projects/${projectId}/metrics?limit=50`),
        apiFetch<SchemaPageResponseArtifactOut>(`/api/v1/projects/${projectId}/artifacts?limit=50`),
      ])
      timeline.value = timelinePage.items
      snapshots.value = snapshotPage.items
      learnings.value = learningPage.items
      experiments.value = experimentPage.items
      observations.value = observationPage.items
      decisions.value = decisionPage.items
      metrics.value = metricPage.items
      artifacts.value = artifactPage.items
    } catch (err) {
      error.value = formatApiError(err, 'failed to load project data')
    } finally {
      loading.value = false
    }
  }

  return {
    timeline,
    snapshots,
    learnings,
    experiments,
    observations,
    decisions,
    metrics,
    artifacts,
    loading,
    error,
    refresh,
  }
})
