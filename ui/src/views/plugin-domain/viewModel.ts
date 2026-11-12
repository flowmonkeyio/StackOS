export function domainLocation(projectId: number, pluginSlug: string) {
  return `/projects/${projectId}/plugins/${encodeURIComponent(pluginSlug)}`
}
export function workflowLocation(projectId: number, pluginSlug: string, key: string) {
  return `${domainLocation(projectId, pluginSlug)}/workflows/${encodeURIComponent(key)}`
}
