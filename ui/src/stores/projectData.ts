import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import type { SchemaDecisionOut, SchemaExperimentOut, SchemaLearningOut } from '@/api'
import { createSourcePager } from '@/lib/sourcePager'
import { useStackOsResourcesStore } from './stackosResources'

export const useProjectDataStore = defineStore('projectData', () => {
  const source = <T extends { id: number }>(path: string) => createSourcePager<T>((id) => `/api/v1/projects/${id}/${path}`)
  const sources = {
    learnings: source<SchemaLearningOut>('learnings'),
    experiments: source<SchemaExperimentOut>('experiments'),
    decisions: source<SchemaDecisionOut>('decisions'),
  }
  const resources = useStackOsResourcesStore()
  const currentProjectId = ref<number | null>(null)
  const artifactScopeCurrent = computed(() => resources.currentProjectId === currentProjectId.value)
  const artifacts = computed(() => artifactScopeCurrent.value ? resources.artifacts : [])
  const pages = reactive({
    learnings: sources.learnings.page, experiments: sources.experiments.page,
    decisions: sources.decisions.page,
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
  function loadMore(kind: keyof typeof sources) { return sources[kind].loadMore() }
  function reset() {
    Object.values(sources).forEach((reader) => reader.reset())
    currentProjectId.value = null
    resources.reset()
  }
  return {
    learnings: sources.learnings.items, experiments: sources.experiments.items,
    decisions: sources.decisions.items, artifacts, pages,
    loading, error, currentProjectId, refreshSource, loadMore, reset,
  }
})
