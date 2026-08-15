import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView, useRoute } from 'vue-router'

import {
  useProjectScopedLoader,
  type ProjectScopedLoadContext,
} from './useProjectScopedLoader'
import { useProjectRouteScope } from './useProjectRouteScope'

describe('useProjectScopedLoader', () => {
  it('starts the scoped load before the first render', async () => {
    const events: string[] = []
    const Probe = defineComponent({
      setup() {
        useProjectScopedLoader({
          projectId: computed(() => 7),
          load: () => events.push('load'),
        })
        return () => {
          events.push('render')
          return null
        }
      },
    })

    const wrapper = mount(Probe)

    expect(events).toEqual(['load', 'render'])
    wrapper.unmount()
  })

  it('loads once per keyed project scope and runs scope cleanup', async () => {
    let releaseProjectOne!: () => void
    const projectOneGate = new Promise<void>((resolve) => {
      releaseProjectOne = resolve
    })
    const contexts: ProjectScopedLoadContext[] = []
    const exits: number[] = []

    const RoutedProbe = defineComponent({
      setup() {
        const { projectId } = useProjectRouteScope()
        useProjectScopedLoader({
          projectId,
          async load(context) {
            contexts.push(context)
            if (context.projectId === 1) await projectOneGate
          },
          onScopeExit: () => exits.push(projectId.value),
        })
        return () => null
      },
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:id', component: RoutedProbe }],
    })
    await router.push('/projects/1')
    await router.isReady()
    const wrapper = mount(
      defineComponent({
        setup() {
          const route = useRoute()
          return () => h(RouterView, { key: String(route.params.id) })
        },
      }),
      { global: { plugins: [router] } },
    )
    await flushPromises()

    expect(contexts.map((context) => context.projectId)).toEqual([1])
    await router.push('/projects/2')
    await flushPromises()
    expect(contexts.map((context) => context.projectId)).toEqual([1, 2])
    expect(exits).toHaveLength(1)

    releaseProjectOne()
    await flushPromises()

    wrapper.unmount()
    expect(exits).toHaveLength(2)
  })

  it('supports manual refresh while the scope is mounted', async () => {
    const contexts: ProjectScopedLoadContext[] = []
    let refresh!: () => Promise<boolean>
    const Probe = defineComponent({
      setup() {
        const projectId = computed(() => 7)
        const loader = useProjectScopedLoader({
          projectId,
          immediate: false,
          load: (context) => contexts.push(context),
        })
        refresh = loader.refresh
        return () => null
      },
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: Probe }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(
      defineComponent({ setup: () => () => h(RouterView) }),
      { global: { plugins: [router] } },
    )

    await expect(refresh()).resolves.toBe(true)
    await expect(refresh()).resolves.toBe(true)
    expect(contexts.map((context) => context.projectId)).toEqual([7, 7])

    wrapper.unmount()
    await expect(refresh()).resolves.toBe(false)
  })

  it('does not load an invalid project scope', async () => {
    const load = vi.fn()
    const Probe = defineComponent({
      setup() {
        useProjectScopedLoader({
          projectId: computed(() => Number.NaN),
          load,
        })
        return () => null
      },
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: Probe }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(
      defineComponent({ setup: () => () => h(RouterView) }),
      { global: { plugins: [router] } },
    )
    await flushPromises()

    expect(load).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
