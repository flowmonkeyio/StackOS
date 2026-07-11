import catalog from '~/data/library-catalog.generated.json'

export interface CatalogReference {
  key: string
  slug: string
  name: string
}

export interface WorkflowStage {
  id: string
  title: string
  summary: string
}

export interface CatalogBase {
  key: string
  slug: string
  name: string
  description: string
  domain: string
  audience: string
  color: string
  featured: boolean
}

export interface WorkflowCatalogItem extends CatalogBase {
  whenToUse: string[]
  stages: WorkflowStage[]
  agentNames: string[]
  agentRefs: CatalogReference[]
  integrations: string[]
}

export interface AgentCatalogItem extends CatalogBase {
  role: string
  workflowKeys: string[]
}

export interface OrchestratorCatalogItem extends CatalogBase {
  workflowKeys: string[]
  coordinates: string[]
  agentRefs: CatalogReference[]
}

const workflows = catalog.workflows as WorkflowCatalogItem[]
const agents = catalog.agents as AgentCatalogItem[]
const orchestrators = catalog.orchestrators as OrchestratorCatalogItem[]

export function useLibraryCatalog() {
  return {
    workflows,
    agents,
    orchestrators,
    workflowBySlug: (slug: string) => workflows.find((item) => item.slug === slug),
    agentBySlug: (slug: string) => agents.find((item) => item.slug === slug),
    orchestratorBySlug: (slug: string) => orchestrators.find((item) => item.slug === slug),
  }
}
