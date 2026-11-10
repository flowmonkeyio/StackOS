import type { Tone } from '@/design/status'

export const taskPriorities = ['p0', 'p1', 'p2', 'p3'] as const
export type TaskPriority = typeof taskPriorities[number]
const tones: Record<TaskPriority, Tone> = { p0: 'danger', p1: 'warning', p2: 'info', p3: 'neutral' }

export function taskPriority(priority: string): { label: string; tone: Tone } {
  return { label: priority.toUpperCase(), tone: tones[priority as TaskPriority] ?? 'neutral' }
}
