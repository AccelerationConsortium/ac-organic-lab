import { describe, it, expect } from 'vitest'
import workflowJson from '../../fixtures/oer_workflow_stub.json'
import type { WorkflowDefinition } from './workflow'

const wf = workflowJson as WorkflowDefinition

describe('WorkflowDefinition shape', () => {
  it('has workflow_name', () => expect(wf.workflow_name).toBe('Test'))
  it('has phases array',   () => expect(Array.isArray(wf.phases)).toBe(true))
  it('first phase has steps', () => expect(wf.phases[0].steps?.length).toBeGreaterThan(0))
  it('second phase has parallel_threads', () => expect(wf.phases[1].parallel_threads?.length).toBeGreaterThan(0))
  it('last phase is teardown', () => expect(wf.phases[wf.phases.length - 1].phase_name).toBe('teardown'))
})
