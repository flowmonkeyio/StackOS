import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import type { SchemaResourceRecordOut } from '@/api'
import ResourceViewRenderer from './ResourceViewRenderer.vue'

describe('ResourceViewRenderer', () => {
  it('reads only declared fields before Details and ignores unknown hints without inventing status', () => {
    const record = { id: 3, project_id: 1, plugin_slug: 'engineering', resource_id: 4,
      resource_key: 'engineering-evidence', title: 'Historical evidence',
      data_json: { summary: 'Recorded proof', lifecycle_state: 'superseded', nested: { token: 'hidden' }, other: 'raw only' },
      provenance_json: null, external_id: null, created_at: '2026-01-01', updated_at: '2026-01-02' }
    const resource = { id: 4, plugin_id: 1, plugin_slug: 'engineering', key: 'engineering-evidence', name: 'Evidence', description: '', schema_json: {}, config_json: null,
      ui_schema_json: { summary_fields: ['summary', 'missing', 'nested'], table_columns: ['lifecycle_state'], detail_sections: [{ title: 'Proof', fields: ['summary', 'nested'] }, { title: 'Unknown', fields: [null, { path: 'other' }] }] } }
    const w = mount(ResourceViewRenderer, { props: { record, resource } })
    expect(w.get('[aria-label="Saved content"]').text()).toContain('Recorded proof')
    expect(w.get('[aria-label="Saved content"]').text()).toContain('superseded')
    expect(w.get('[aria-label="Saved content"]').text()).not.toContain('raw only')
    expect(w.text()).not.toContain('hidden')
    expect(w.get('details').text()).toContain('raw only')
    expect(w.get('details').text()).toContain('resource_record')
  })
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
