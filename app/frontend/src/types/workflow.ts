export interface WorkflowStep {
  step_id: string
  action: string
  params: Record<string, unknown> | string
  description?: string
}

export interface WorkflowPhase {
  phase_name: string
  description?: string
  for_each_spot?: boolean
  steps?: WorkflowStep[]
  parallel_threads?: { thread_name: string; steps: WorkflowStep[] }[]
}

export interface WorkflowDefinition {
  workflow_name: string
  version: string
  description?: string
  phases: WorkflowPhase[]
}

export interface LabCanvasNode {
  id: string
  label: string
  description?: string
  for_each_spot?: boolean
  stepCount: number
  dependencies: string[]
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'warning'
  nodeType?: 'setup' | 'phase' | 'teardown' | 'checkpoint'
}
