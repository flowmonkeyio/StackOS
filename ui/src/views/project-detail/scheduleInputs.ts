import type { SchemaLoadedWorkflowTemplate } from '@/api'

export type InputValues = Record<string, unknown>
export interface InputField {
  key: string
  label: string
  help: string
  required: boolean
  type: string
  schema: InputValues
  options?: unknown[]
  depth: number
  listItem?: boolean
}
export const isObject = (value: unknown): value is InputValues => !!value && typeof value === 'object' && !Array.isArray(value)
export const missingInput = (value: unknown): boolean => value == null || value === '' || (Array.isArray(value) && !value.length) || (isObject(value) && !Object.keys(value).length)
const equal = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right)

// Match run_plan_template_builder: defaults are shallow, and explicit false/0/empty win.
export function effectiveInputs(workflow: SchemaLoadedWorkflowTemplate | null, overrides: InputValues): InputValues {
  const defaults = Object.fromEntries((workflow?.spec.inputs ?? []).filter(item => item.default_json != null).map(item => [item.key, item.default_json]))
  return { ...defaults, ...(workflow?.project_extension?.enabled ? workflow.project_extension.input_defaults_json : {}), ...overrides }
}
export function workflowFields(workflow: SchemaLoadedWorkflowTemplate): InputField[] {
  const required = workflow.project_extension?.enabled ? workflow.project_extension.required_input_keys_json ?? [] : []
  const fields = (workflow.spec.inputs ?? []).map(item => ({
    key: item.key, label: item.name || item.key, help: item.description,
    required: item.required || required.includes(item.key), type: item.type,
    schema: item.schema_json ?? {}, options: item.options_json ?? undefined, depth: 0,
  }))
  for (const key of required) if (!fields.some(field => field.key === key)) fields.push({ key, label: key, help: '', required: true, type: 'object', schema: {}, options: undefined, depth: 0 })
  return fields
}
export function propertyFields(field: InputField): InputField[] {
  if (field.depth >= 3 || !isObject(field.schema.properties)) return []
  // Large/open structures stay with workflow setup rather than growing a schema editor.
  const entries = Object.entries(field.schema.properties)
  const required = Array.isArray(field.schema.required) ? field.schema.required.filter((key): key is string => typeof key === 'string') : []
  const keys = new Set([...entries.map(([key]) => key), ...required])
  if (keys.size > 30) return []
  return [...keys].map(key => schemaField(key, (field.schema.properties as InputValues)[key], field.depth + 1, required.includes(key)))
}
export function schemaField(key: string, raw: unknown, depth: number, required = false): InputField {
  const schema = isObject(raw) ? raw : {}
  return { key, label: typeof schema.title === 'string' ? schema.title : key, help: typeof schema.description === 'string' ? schema.description : '', type: typeof schema.type === 'string' ? schema.type : 'opaque', schema, required, depth }
}
export function itemField(field: InputField, index: number): InputField {
  return { ...schemaField(`${index + 1}`, field.schema.items, field.depth + 1), listItem: true }
}
export function fieldKind(field: InputField): string {
  if (field.depth > 3 || ['oneOf', 'anyOf', 'allOf', '$ref'].some(key => key in field.schema)) return 'opaque'
  if (field.options?.length || Array.isArray(field.schema.enum)) return 'option'
  if (['string', 'text', 'multiline', 'number', 'integer', 'boolean'].includes(field.type)) return field.type
  if (field.type === 'object' && propertyFields(field).length) return 'object'
  if (['array', 'list'].includes(field.type) && isObject(field.schema.items) && fieldKind(itemField(field, 0)) !== 'opaque') return 'array'
  return 'opaque'
}
export function fieldOptions(field: InputField): unknown[] { return field.options ?? (Array.isArray(field.schema.enum) ? field.schema.enum : []) }
export function valueSummary(value: unknown): string {
  if (value == null) return 'Not supplied'
  if (Array.isArray(value)) return `Saved list (${value.length} items)`
  if (isObject(value)) return `Saved structured value (${Object.keys(value).length} fields)`
  return String(value)
}
export function fieldError(field: InputField, value: unknown, enabling: boolean): string | null {
  const kind = fieldKind(field)
  if (missingInput(value)) {
    if (field.required && enabling) return kind === 'opaque' ? 'Required data is missing. Complete workflow setup before enabling; you can save a disabled draft.' : 'This field is required before enabling.'
    // A list slot is a supplied value, even when its control is blank. Validate
    // it against the item type instead of treating it as an omitted input.
    if (value == null && !field.listItem) return null
  }
  if (kind === 'opaque') return null // Canonical server validation owns unsupported schemas.
  if (kind === 'option' && !fieldOptions(field).some(option => equal(option, value))) return 'Choose one of the declared options.'
  if (['string', 'text', 'multiline'].includes(kind)) {
    if (typeof value !== 'string') return 'A text value is required. The saved value is preserved.'
    if (typeof field.schema.minLength === 'number' && value.length < field.schema.minLength) return `Use at least ${field.schema.minLength} characters.`
    if (typeof field.schema.maxLength === 'number' && value.length > field.schema.maxLength) return `Use at most ${field.schema.maxLength} characters.`
  }
  if (['number', 'integer'].includes(kind)) {
    if (typeof value !== 'number' || !Number.isFinite(value) || (kind === 'integer' && !Number.isInteger(value))) return `Enter a valid ${kind}.`
    if (typeof field.schema.minimum === 'number' && value < field.schema.minimum) return `Use ${field.schema.minimum} or more.`
    if (typeof field.schema.maximum === 'number' && value > field.schema.maximum) return `Use ${field.schema.maximum} or less.`
  }
  if (kind === 'boolean' && typeof value !== 'boolean') return 'Choose a boolean value.'
  if (kind === 'object') {
    if (!isObject(value)) return 'A structured value is required. The saved value is preserved.'
    for (const child of propertyFields(field)) {
      const error = fieldError(child, value[child.key], enabling)
      if (error) return `${child.label}: ${error}`
    }
  }
  if (kind === 'array') {
    if (!Array.isArray(value)) return 'A list is required. The saved value is preserved.'
    if (typeof field.schema.minItems === 'number' && value.length < field.schema.minItems) return `Add at least ${field.schema.minItems} items.`
    if (typeof field.schema.maxItems === 'number' && value.length > field.schema.maxItems) return `Use at most ${field.schema.maxItems} items.`
    if (field.schema.uniqueItems && new Set(value.map(item => JSON.stringify(item))).size !== value.length) return 'Each item must be unique.'
    for (const [index, item] of value.entries()) {
      const error = fieldError(itemField(field, index), item, enabling)
      if (error) return `Item ${index + 1}: ${error}`
    }
  }
  return null
}
