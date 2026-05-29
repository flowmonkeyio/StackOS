import { apiFetch } from '@/lib/client'

export async function callOperation<T>(
  operationName: string,
  argumentsJson: Record<string, unknown>,
): Promise<T> {
  const operationArguments = {
    response_mode: 'raw',
    ...argumentsJson,
  }
  return apiFetch<T>(`/api/v1/operations/${operationName}/call`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ arguments: operationArguments }),
  })
}
