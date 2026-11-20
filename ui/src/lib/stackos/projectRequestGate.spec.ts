// @vitest-environment node

import { describe, expect, it, vi } from 'vitest'

import { createProjectRequestGate } from './projectRequestGate'

describe('createProjectRequestGate', () => {
  it('captures an existing scope without claiming it and fences exit/return lifetimes', () => {
    const gate = createProjectRequestGate()
    const absent = gate.captureScope(1)
    gate.begin(1, 'connections')
    expect(absent()).toBe(false)
    const current = gate.captureScope(1)
    gate.begin(1, 'connections')
    expect(current()).toBe(true)
    gate.begin(2, 'connections')
    gate.begin(1, 'connections')
    expect(current()).toBe(false)
    const returned = gate.captureScope(1)
    gate.invalidate()
    gate.begin(1, 'connections')
    expect(returned()).toBe(false)
  })
  it('invalidates every old request when the project changes', () => {
    const onScopeChange = vi.fn()
    const gate = createProjectRequestGate(onScopeChange)
    const projectOneList = gate.begin(1, 'list')
    const projectOneDetail = gate.begin(1, 'detail')
    const projectTwoList = gate.begin(2, 'list')

    expect(projectOneList.isCurrent()).toBe(false)
    expect(projectOneDetail.isCurrent()).toBe(false)
    expect(projectTwoList.isCurrent()).toBe(true)
    expect(onScopeChange).toHaveBeenCalledTimes(2)
  })

  it('allows different operations to run together but keeps each latest-wins', () => {
    const gate = createProjectRequestGate()
    const firstList = gate.begin(1, 'list')
    const detail = gate.begin(1, 'detail')
    const secondList = gate.begin(1, 'list')

    expect(firstList.isCurrent()).toBe(false)
    expect(detail.isCurrent()).toBe(true)
    expect(secondList.isCurrent()).toBe(true)
    expect(firstList.finish()).toBe(true)
    expect(detail.finish()).toBe(true)
    expect(secondList.finish()).toBe(false)
  })

  it('invalidates outstanding work explicitly', () => {
    const gate = createProjectRequestGate()
    const request = gate.begin(1)

    gate.invalidate()

    expect(request.isCurrent()).toBe(false)
    expect(request.finish()).toBe(false)
  })

  it('can invalidate one operation without disturbing its siblings', () => {
    const gate = createProjectRequestGate()
    const list = gate.begin(1, 'list')
    const detail = gate.begin(1, 'detail')

    gate.invalidateOperation('detail')

    expect(list.isCurrent()).toBe(true)
    expect(detail.isCurrent()).toBe(false)
  })

  it('rejects invalid project identifiers', () => {
    const gate = createProjectRequestGate()

    expect(() => gate.begin(0)).toThrow(TypeError)
    expect(() => gate.begin(Number.NaN)).toThrow(TypeError)
  })
})
