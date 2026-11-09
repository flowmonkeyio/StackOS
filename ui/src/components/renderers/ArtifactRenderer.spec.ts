import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import type { SchemaArtifactOut } from '@/api'
import ArtifactRenderer from './ArtifactRenderer.vue'

const artifact = (uri: string, overrides: Partial<SchemaArtifactOut> = {}): SchemaArtifactOut => ({
  id: 1, project_id: 1, plugin_id: null, plugin_slug: null, kind: 'image',
  name: 'Saved image', uri, mime_type: 'image/png', size_bytes: 0,
  resource_record_id: null, status: 'superseded', superseded_by_artifact_id: 2,
  created_at: '2026-01-01', updated_at: '2026-01-02', metadata_json: null,
  provenance_json: null, ...overrides,
})

describe('ArtifactRenderer reference capabilities', () => {
  it('offers an explicit safe open reference and opt-in image preview without claiming availability', async () => {
    const w = mount(ArtifactRenderer, { props: { artifact: artifact('https://example.com/image.png') } })
    expect(w.find('a').attributes('href')).toBe('https://example.com/image.png')
    expect(w.find('a').attributes('rel')).toContain('noreferrer')
    expect(w.find('img').exists()).toBe(false)
    expect(w.text()).toContain('superseded')
    await w.get('button').trigger('click')
    expect(w.get('img').attributes('referrerpolicy')).toBe('no-referrer')
    await w.get('img').trigger('error')
    expect(w.text()).toContain('Preview unavailable')
  })

  it.each(['javascript:alert(1)', 'file:///private/secret.png', 's3://bucket/file', '', 'https://user:pass@example.com/x', 'https://example.com/?token=secret'])('retains metadata without unsafe actions for %s', (uri) => {
    const w = mount(ArtifactRenderer, { props: { artifact: artifact(uri) } })
    expect(w.find('a').exists()).toBe(false)
    expect(w.find('img').exists()).toBe(false)
    expect(w.text()).toContain('Reference unavailable')
    expect(w.text()).not.toContain('token=secret')
  })

  it('keeps Finance originals private even when the stored URI is HTTPS', () => {
    const w = mount(ArtifactRenderer, { props: { artifact: artifact('https://example.com/original.png', { plugin_slug: 'finance' }) } })
    expect(w.find('a').exists()).toBe(false)
    expect(w.find('img').exists()).toBe(false)
    expect(w.text()).not.toContain('https://example.com/original.png')
    expect(w.text()).toContain('Finance')
  })
})
