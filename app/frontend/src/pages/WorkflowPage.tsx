import { useState } from 'react'
import type { WorkflowDefinition, LabCanvasNode } from '../types/workflow'
import styles from './WorkflowPage.module.css'

function phaseToNode(
  phase: WorkflowDefinition['phases'][0],
  index: number,
  allPhases: WorkflowDefinition['phases'],
): LabCanvasNode {
  const stepCount =
    (phase.steps?.length ?? 0) +
    (phase.parallel_threads?.reduce((s, t) => s + t.steps.length, 0) ?? 0)
  return {
    id: phase.phase_name,
    label: phase.phase_name.replace(/_/g, ' '),
    description: phase.description,
    for_each_spot: phase.for_each_spot,
    stepCount,
    dependencies: index > 0 ? [allPhases[index - 1].phase_name] : [],
    status: 'pending',
    nodeType: index === 0 ? 'setup' : index === allPhases.length - 1 ? 'teardown' : 'phase',
  }
}

export default function WorkflowPage() {
  const [workflow, setWorkflow] = useState<WorkflowDefinition | null>(null)
  const [selectedPhase, setSelectedPhase] = useState<string | null>(null)

  function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => {
      try {
        setWorkflow(JSON.parse(ev.target?.result as string))
        setSelectedPhase(null)
      } catch {
        alert('Invalid JSON')
      }
    }
    reader.readAsText(file)
  }

  const nodes: LabCanvasNode[] = workflow
    ? workflow.phases.map((p, i) => phaseToNode(p, i, workflow.phases))
    : []

  const selected = workflow?.phases.find(p => p.phase_name === selectedPhase)

  return (
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <h1 className={styles.heading}>Workflow Canvas</h1>
        <label className={styles.uploadBtn}>
          Load JSON
          <input type="file" accept=".json" hidden onChange={handleFileUpload} />
        </label>
      </div>

      {!workflow && (
        <div className={styles.empty}>
          Upload an <code>oer_workflow.json</code> (or any lab workflow JSON) to visualise phases.
        </div>
      )}

      {workflow && (
        <div className={styles.layout}>
          <div className={styles.phaseList}>
            {nodes.map(node => (
              <button
                key={node.id}
                className={`${styles.phaseNode} ${selectedPhase === node.id ? styles.selected : ''}`}
                onClick={() => setSelectedPhase(node.id)}
              >
                <span className={styles.phaseName}>{node.label}</span>
                <div className={styles.phaseMeta}>
                  {node.for_each_spot && <span className={styles.tag}>per-spot</span>}
                  <span className={styles.stepCount}>{node.stepCount} steps</span>
                  {node.nodeType === 'setup' && <span className={styles.typeTag}>setup</span>}
                  {node.nodeType === 'teardown' && <span className={styles.typeTag}>teardown</span>}
                </div>
                {node.dependencies.length > 0 && (
                  <span className={styles.dep}>← {node.dependencies[0].replace(/_/g, ' ')}</span>
                )}
              </button>
            ))}
          </div>

          {selected && (
            <div className={styles.detail}>
              <h2 className={styles.detailTitle}>{selected.phase_name.replace(/_/g, ' ')}</h2>
              {selected.description && (
                <p className={styles.detailDesc}>{selected.description}</p>
              )}

              {selected.steps && selected.steps.length > 0 && (
                <table className={styles.stepTable}>
                  <thead><tr><th>Step</th><th>Action</th><th>Description</th></tr></thead>
                  <tbody>
                    {selected.steps.map(step => (
                      <tr key={step.step_id}>
                        <td className={styles.mono}>{step.step_id}</td>
                        <td className={styles.mono}>{step.action}</td>
                        <td>{step.description ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {selected.parallel_threads && selected.parallel_threads.length > 0 && (
                <div className={styles.threads}>
                  {selected.parallel_threads.map(thread => (
                    <div key={thread.thread_name} className={styles.thread}>
                      <h3 className={styles.threadName}>{thread.thread_name}</h3>
                      <table className={styles.stepTable}>
                        <thead><tr><th>Step</th><th>Action</th><th>Description</th></tr></thead>
                        <tbody>
                          {thread.steps.map(step => (
                            <tr key={step.step_id}>
                              <td className={styles.mono}>{step.step_id}</td>
                              <td className={styles.mono}>{step.action}</td>
                              <td>{step.description ?? '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {!selected && (
            <div className={styles.detailEmpty}>← Select a phase to see its steps</div>
          )}
        </div>
      )}
    </div>
  )
}
