import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import type { TrackerTask } from '@/lib/task-tracker/types'

import TrackerTaskDetailDialog from './TrackerTaskDetailDialog.vue'

describe('TrackerTaskDetailDialog', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('shows when task contexts and files are truncated previews', async () => {
    const wrapper = mount(TrackerTaskDetailDialog, {
      props: {
        modelValue: true,
        task: task(),
        contexts: [
          {
            id: 1,
            context_ref: 'ctx_reporting',
            name: 'Reporting context',
            plugin_slug: 'provider',
            provider_key: 'provider',
            status: 'active',
            artifact_count: 12,
          },
        ],
        contextPageInfo: {
          limit: 20,
          nextCursor: 20,
          totalEstimate: 24,
        },
        contextArtifacts: {
          ctx_reporting: [
            {
              id: 1,
              context_ref: 'ctx_reporting',
              artifact_id: 7,
              action_call_id: 12,
              semantic_name: 'current-results.json',
              action_ref: 'provider.reporting.list',
              metadata_json: {
                absolute_path: '/tmp/current-results.json',
                bytes: 4096,
                content_type: 'application/json',
              },
              created_at: '2026-06-06T00:00:00Z',
              artifact: {
                id: 7,
                name: 'current-results.json',
                uri: '/tmp/current-results.json',
              },
            },
          ],
        },
        contextArtifactPageInfo: {
          ctx_reporting: {
            limit: 5,
            nextCursor: 5,
            totalEstimate: 12,
          },
        },
      },
      attachTo: document.body,
    })

    await nextTick()

    expect(document.body.textContent).toContain('Showing first 1 of 24 contexts.')
    expect(document.body.textContent).toContain('Reporting context')
    expect(document.body.textContent).toContain('current-results.json')
    expect(document.body.textContent).toContain('Showing first 1 of 12 files.')

    wrapper.unmount()
  })
})

function task(): TrackerTask {
  return {
    id: 76,
    project_id: 1,
    tracker_id: 1,
    key: 'workflow-39',
    title: 'Platform UI drawers',
    goal: 'Polish task drawer details.',
    description: 'Polish task drawer details.',
    status: 'in-progress',
    priority_key: 'p1',
    lane_key: 'implementation',
    owner: 'codex',
    task_type: 'workflow',
    order_index: 1,
    source_kind: 'workflow',
    source_json: { run_plan_id: 39 },
    definition_of_done_json: ['Drawer renders context previews truthfully.'],
    constraints_json: [],
    expected_outcomes_json: [],
    completion_evidence_json: null,
    context_json: null,
    metadata_json: null,
    created_by: 'codex',
    created_at: '2026-06-06T00:00:00Z',
    updated_at: '2026-06-06T00:00:00Z',
    started_at: '2026-06-06T00:00:00Z',
    completed_at: null,
  }
}
