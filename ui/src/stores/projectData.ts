import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import type {
  SchemaContextSnapshotOut, SchemaDecisionOut, SchemaExperimentObservationOut,
  SchemaExperimentOut, SchemaLearningOut, SchemaMetricSnapshotOut, SchemaProjectEventOut,
} from '@/api'
import { createSourcePager } from '@/lib/sourcePager'
import { useStackOsResourcesStore } from './stackosResources'

export const useProjectDataStore = defineStore('projectData', () => {
  const source = <T extends { id: number }>(path: string) => createSourcePager<T>((id) => `/api/v1/projects/${id}/${path}`)
  const sources = {
    timeline: source<SchemaProjectEventOut>('context/timeline'),
    snapshots: source<SchemaContextSnapshotOut>('context/snapshots'),
    learnings: source<SchemaLearningOut>('learnings'),
    experiments: source<SchemaExperimentOut>('experiments'),
    observations: source<SchemaExperimentObservationOut>('experiments/observations'),
    decisions: source<SchemaDecisionOut>('decisions'),
    metrics: source<SchemaMetricSnapshotOut>('metrics'),
  }
  const resources = useStackOsResourcesStore()
  const currentProjectId = ref<number | null>(null)
  const artifactScopeCurrent = computed(() => resources.currentProjectId === currentProjectId.value)
  const artifacts = computed(() => artifactScopeCurrent.value ? resources.artifacts : [])
  const pages = reactive({
    timeline: sources.timeline.page, snapshots: sources.snapshots.page,
    learnings: sources.learnings.page, experiments: sources.experiments.page,
    observations: sources.observations.page, decisions: sources.decisions.page,
    metrics: sources.metrics.page,
  })
  const loading = computed(() => Object.values(pages).some((page) => page.loading) || artifactScopeCurrent.value && resources.artifactPage.loading)
  const error = computed(() => Object.values(pages).find((page) => page.error)?.error || (artifactScopeCurrent.value ? resources.artifactPage.error : null))

  function ensureScope(projectId: number) {
    if (currentProjectId.value === projectId) return
    Object.values(sources).forEach((reader) => reader.reset())
    currentProjectId.value = projectId
  }
  /** Independent reads let mixed lists retry one failed source without losing the others. */
  function refreshSource(projectId: number, kind: keyof typeof sources) {
    ensureScope(projectId)
    return sources[kind].refresh(projectId)
  }
  async function refresh(projectId: number) {
    const firstRead = currentProjectId.value !== projectId
    ensureScope(projectId)
    await Promise.all([
      ...Object.values(sources).map((reader) => reader.refresh(projectId)),
      resources.refreshArtifacts(projectId, {}, firstRead),
    ])
  }
  function loadMore(kind: keyof typeof sources) { return sources[kind].loadMore() }
  function reset() {
    Object.values(sources).forEach((reader) => reader.reset())
    currentProjectId.value = null
    resources.reset()
  }
  return {
    timeline: sources.timeline.items, snapshots: sources.snapshots.items,
    learnings: sources.learnings.items, experiments: sources.experiments.items,
    observations: sources.observations.items, decisions: sources.decisions.items,
    metrics: sources.metrics.items, artifacts, pages,
    loading, error, currentProjectId, refresh, refreshSource, loadMore, reset,
  }
})
