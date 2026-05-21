import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import type { SchemaResourceRecordOut } from '@/api'
import ResourceViewRenderer from './ResourceViewRenderer.vue'

describe('ResourceViewRenderer', () => {
  it('uses explicit record fields and redacts payload secrets', () => {
    const record: SchemaResourceRecordOut = {
      id: 12,
      project_id: 1,
      resource_id: 3,
      plugin_slug: 'core',
      resource_key: 'learning',
      external_id: 'lesson-1',
      title: 'Lesson',
      data_json: { body: 'Use short hooks.', api_key: 'secret' },
      provenance_json: { source: 'run', token: 'secret' },
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
    }

    const w = mount(ResourceViewRenderer, { props: { record } })

    expect(w.text()).toContain('Lesson')
    expect(w.text()).toContain('learning')
    expect(w.text()).not.toContain('secret')
    expect(w.text()).toContain('[redacted]')
  })
})
