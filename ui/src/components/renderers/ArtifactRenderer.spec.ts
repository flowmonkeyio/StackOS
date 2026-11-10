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

  it.each([
    { id: 2 },
    { project_id: 2 },
    { uri: 'https://example.com/replacement.png' },
  ])('resets preview consent and failure when the reference identity changes: %s', async (change) => {
    const original = artifact('https://example.com/image.png')
    const w = mount(ArtifactRenderer, { props: { artifact: original } })
    await w.get('button').trigger('click')
    await w.setProps({ artifact: { ...original, name: 'Updated description' } })
    expect(w.find('img').exists()).toBe(true)

    await w.setProps({ artifact: { ...original, ...change } })
    expect(w.find('img').exists()).toBe(false)
    await w.get('button').trigger('click')
    await w.get('img').trigger('error')
    expect(w.text()).toContain('Preview unavailable')

    await w.setProps({ artifact: original })
    expect(w.text()).not.toContain('Preview unavailable')
    expect(w.find('img').exists()).toBe(false)
    await w.get('button').trigger('click')
    expect(w.get('img').attributes('src')).toBe(original.uri)
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

  it.each([
    'ftp://review-user:review-password@example.invalid/file',
    's3://review-user:review-password@example.invalid/file',
    ' https://review-user:review-password@example.invalid/file',
    '//review-user:review-password@example.invalid/file',
    '\tftp://review-user:review-password@example.invalid/file\r\n',
    'ht\ttps://review-user:review-password@example.invalid/file',
    '\\\\review-user:review-password@example.invalid/file',
    ' ftp://review-user:review-password@',
    '///review-user:review-password@example.invalid/file',
    'https://example.invalid/image.png?sig=review-signature&sp=r&se=2099-01-01',
    'https://example.invalid/image.png?SIG=review-signature',
    'https://example.invalid/image.png?path=?&s%69g=review-signature',
    's3://example.invalid/file?X-Amz-Signature=review-signature',
  ])('withholds credentials and signed capabilities in actions and Details: %s', (uri) => {
    const row = artifact(uri)
    const w = mount(ArtifactRenderer, { props: { artifact: row } })
    expect(w.find('a').exists()).toBe(false)
    expect(w.findAll('button').some((button) => button.text().includes('Preview'))).toBe(false)
    expect(w.find('img').exists()).toBe(false)
    expect(w.get('details').text()).not.toContain('review-password')
    expect(w.get('details').text()).not.toContain('review-signature')
    expect(w.get('details').text()).toContain('[redacted reference]')
    expect(row.uri).toBe(uri)
  })

  it.each(['ftp://example.invalid/file', 's3://example.invalid/file', 'file:///private/example.png', '//example.invalid/file', ' https://example.invalid/file'])('preserves harmless unsupported reference metadata: %s', (uri) => {
    const w = mount(ArtifactRenderer, { props: { artifact: artifact(uri) } })
    expect(w.find('a').exists()).toBe(false)
    expect(w.get('details').text()).toContain(uri)
  })
})
