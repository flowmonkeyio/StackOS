import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { toLibrarySource } from '@/lib/librarySources'
import SourceDetailRenderer from './SourceDetailRenderer.vue'

describe('shared source details', () => {
  it('retains historical core learning state and unlinked identity without declaring currency', () => {
    const source = toLibrarySource('learning', {
      id: 8, project_id: 2, statement: 'Keep the original lesson', status: 'superseded',
      review_state: 'unreviewed', confidence: 'low', domain: null,
      source_snapshot_id: null, supersedes_learning_id: null,
      evidence_json: { api_key: 'private-evidence' },
    } as never)
    const w = mount(SourceDetailRenderer, { props: { source } })
    expect(w.get('[aria-label="Saved content"]').text()).toContain('superseded')
    expect(w.text()).toContain('unreviewed')
    expect(w.text()).not.toContain('private-evidence')
    expect(w.text()).not.toContain('Current evidence')
    expect(w.get('details').text()).toContain('learning:8')
    expect(w.get('details').text()).toContain('"project_id": 2')
  })

  it('keeps meaningful experiment content and nested long arrays readable before source details', () => {
    const source = toLibrarySource('experiment', {
      id: 9, project_id: 2, hypothesis: 'A documented experiment', status: 'draft',
      variants: [{ name: 'Long variant', config_json: { token: 'private-variant', notes: Array.from({ length: 60 }, (_, i) => `Observation ${i}`) } }],
    } as never)
    const w = mount(SourceDetailRenderer, { props: { source } })
    expect(w.get('[aria-label="Saved content"]').text()).toContain('Observation 59')
    expect(w.get('[aria-label="Saved content"]').text()).toContain('A documented experiment')
    expect(w.text()).not.toContain('private-variant')
    expect(w.get('details').text()).toContain('experiment:9')
  })

  it('falls back to exact source details when the schema belongs to another resource', () => {
    const source = toLibrarySource('resource_record', {
      id: 3, project_id: 2, resource_id: 8, resource_key: 'old-record', plugin_slug: 'engineering',
      data_json: { summary: 'Historical content without schema hints' },
    } as never)
    const w = mount(SourceDetailRenderer, { props: {
      source, resource: { id: 99, key: 'unrelated', plugin_slug: 'engineering', ui_schema_json: { summary_fields: ['summary'] } } as never,
    } })
    expect(w.find('[aria-label="Saved content"]').exists()).toBe(false)
    expect(w.text()).toContain('No readable field hints')
    expect(w.get('details').text()).toContain('Historical content without schema hints')
  })

  it('treats absent prototype-named schema fields as missing while retaining real own fields', () => {
    const source = toLibrarySource('resource_record', {
      id: 3, project_id: 2, resource_id: 8, resource_key: 'old-record', plugin_slug: 'engineering',
      data_json: { summary: 'Recorded content' },
    } as never)
    const resource = { id: 8, key: 'old-record', plugin_slug: 'engineering',
      ui_schema_json: { summary_fields: ['summary'], table_columns: ['constructor', 'toString', 'missing'] } } as never
    const w = mount(SourceDetailRenderer, { props: { source, resource } })
    const facts = w.get('[aria-label="Saved content"]')
    expect(facts.text()).toContain('Recorded content')
    expect(facts.text()).not.toContain('function')
    expect(facts.findAll('dd').slice(1).map((field) => field.text())).toEqual(['-', '-', '-'])
    const ownSource = toLibrarySource('resource_record', {
      ...source.payload, data_json: { constructor: 'Recorded constructor value' },
    })
    const own = mount(SourceDetailRenderer, { props: { source: ownSource, resource } })
    expect(own.get('[aria-label="Saved content"]').text()).toContain('Recorded constructor value')
    expect(w.get('details').text()).toContain('Recorded content')
  })
  it('shows overlapping schema fields once and omits emptied groups', () => {
    const source = toLibrarySource('resource_record', {
      id: 3, project_id: 2, resource_id: 8, resource_key: 'engineering-decisions', plugin_slug: 'engineering',
      data_json: { decision: 'Use the shared source', rationale: 'One owner', status: 'accepted', scope: 'Project', evidence_refs: ['receipt-1'] },
    } as never)
    const resource = { id: 8, key: 'engineering-decisions', plugin_slug: 'engineering', ui_schema_json: {
      summary_fields: ['decision', 'rationale'],
      table_columns: ['status', 'scope', 'decision'],
      detail_sections: [
        { title: 'Decision', fields: ['status', 'decision', 'rationale'] },
        { title: 'Traceability', fields: ['scope', 'evidence_refs'] },
        { title: 'Repeated evidence', fields: ['evidence_refs'] },
      ],
    } } as never
    const w = mount(SourceDetailRenderer, { props: { source, resource } })
    const content = w.get('[aria-label="Saved content"]')
    expect(content.findAll('dt').map(field => field.text())).toEqual([
      'Decision', 'Rationale', 'Status', 'Scope', 'Evidence refs',
    ])
    expect(content.findAll('h3').map(heading => heading.text())).toEqual(['Summary', 'Facts', 'Traceability'])
    expect(w.get('details').text()).toContain('receipt-1')
  })

})
