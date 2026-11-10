import { describe, expect, it } from 'vitest'
import type { SchemaLoadedWorkflowTemplate } from '@/api'
import { effectiveInputs, workflowFields, fieldError, propertyFields } from './scheduleInputs'

const workflow = (inputs: object[], extension = {}): SchemaLoadedWorkflowTemplate => ({ spec: { inputs }, project_extension: extension } as SchemaLoadedWorkflowTemplate)
describe('schedule input contracts', () => {
  it('applies template, enabled project and saved precedence without losing typed or unknown values', () => {
    const loaded = workflow([{ key: 'count', type: 'number', default_json: 4 }, { key: 'flag', type: 'boolean', default_json: true }], { enabled: true, input_defaults_json: { count: 2, flag: true, context: { refs: ['source'] } } })
    expect(effectiveInputs(loaded, { count: 0, flag: false, text: '', unknown: [1, { a: true }] })).toEqual({ count: 0, flag: false, text: '', context: { refs: ['source'] }, unknown: [1, { a: true }] })
    loaded.project_extension!.enabled = false
    expect(effectiveInputs(loaded, {})).toEqual({ count: 4, flag: true })
  })
  it('uses input metadata and extension required keys, including undeclared setup', () => {
    const fields = workflowFields(workflow([{ key: 'goal', name: 'Goal', description: 'Requested outcome', type: 'string', required: true }, { key: 'repo_context', type: 'object' }, { key: 'release_intent', name: 'Release Intent', type: 'string' }], { enabled: true, required_input_keys_json: ['setup'] }))
    expect(fields.map(field => field.label)).toEqual(['Goal', 'repo_context', 'Release Intent', 'setup'])
    expect(fieldError(fields[3]!, undefined, true)).toContain('workflow setup')
    expect(fieldError(fields[3]!, undefined, false)).toBeNull()
  })
  it('checks presence, types, options and declared bounds while retaining valid false, zero and optional empty values', () => {
    const fields = workflowFields(workflow([{ key: 'flag', type: 'boolean', required: true }, { key: 'count', type: 'number', required: true, schema_json: { minimum: 0 } }, { key: 'text', type: 'string' }, { key: 'mode', type: 'string', options_json: ['auto', 'skip'] }]))
    expect(fieldError(fields[0]!, false, true)).toBeNull()
    expect(fieldError(fields[1]!, 0, true)).toBeNull()
    expect(fieldError(fields[2]!, '', true)).toBeNull()
    expect(fieldError(fields[1]!, -1, true)).toBeTruthy()
    expect(fieldError(fields[1]!, '0', true)).toBeTruthy()
    expect(fieldError(fields[3]!, 'other', true)).toBeTruthy()
  })
  it('supports bounded properties and lists and leaves unsupported structure opaque', () => {
    const [field] = workflowFields(workflow([{ key: 'source_scope', type: 'object', schema_json: { properties: { refs: { type: 'array', items: { type: 'string', minLength: 1 } }, count: { type: 'integer' } }, required: ['refs'] } }]))
    expect(propertyFields(field!).map(item => [item.key, item.type, item.required])).toEqual([['refs', 'array', true], ['count', 'integer', false]])
    expect(fieldError(field!, { refs: [''] }, true)).toContain('refs')
    expect(fieldError(field!, { refs: ['source'], count: 1.2 }, true)).toContain('count')
    expect(fieldError(field!, { refs: ['source'], untouched: { exact: true } }, true)).toBeNull()
  })
  it('reports undeclared required properties as workflow setup and bounds deep/open schemas', () => {
    const [field] = workflowFields(workflow([{ key: 'context', type: 'object', schema_json: { properties: { title: { type: 'string' } }, required: ['external'] } }]))
    expect(fieldError(field!, { title: 'Retain' }, true)).toContain('external: Required data is missing')
    expect(fieldError(field!, { title: 'Retain' }, false)).toBeNull()
    expect(propertyFields({ ...field!, depth: 3 })).toEqual([])
  })
})
