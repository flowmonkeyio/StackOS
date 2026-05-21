import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import type { SchemaActionOut } from '@/api'
import ActionSchemaRenderer from './ActionSchemaRenderer.vue'

describe('ActionSchemaRenderer', () => {
  it('renders action schema details without exposing secret-looking config fields', () => {
    const action: SchemaActionOut = {
      id: 1,
      plugin_id: 1,
      plugin_slug: 'utils',
      provider_id: 1,
      provider_key: 'openai-images',
      key: 'image.generate',
      name: 'Generate Image',
      description: 'Generate and persist image artifacts.',
      capability_key: 'image-generation',
      risk_level: 'cost',
      input_schema_json: { type: 'object', required: ['prompt'] },
      output_schema_json: { type: 'object' },
      config_json: { connector: 'openai-images', api_key: 'secret' },
    }

    const w = mount(ActionSchemaRenderer, { props: { action } })

    expect(w.text()).toContain('Generate Image')
    expect(w.text()).toContain('utils')
    expect(w.text()).not.toContain('secret')
    expect(w.text()).toContain('[redacted]')
  })
})
