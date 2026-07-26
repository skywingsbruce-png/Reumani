import { useState } from 'react'
import { useLab } from '../store/LabStore'
import { StepBadge } from '../ui'

export function PlanStepList() {
  const { state } = useLab()
  const [open, setOpen] = useState<Record<string, boolean>>({ 'step-3': true })

  return (
    <section className="section plan" aria-label="计划步骤">
      <h2 className="section-title">计划步骤</h2>
      <ol className="step-list">
        {state.planSteps.map((s) => {
          const isOpen = !!open[s.id]
          return (
            <li key={s.id} className={`step step-${s.status}`}>
              <button className="step-row" aria-expanded={isOpen}
                onClick={() => setOpen((o) => ({ ...o, [s.id]: !o[s.id] }))}>
                <span className="step-num" aria-hidden>{s.index}</span>
                <span className="step-obj">{s.objective}</span>
                <StepBadge status={s.status} />
                <span className="step-caret" aria-hidden>{isOpen ? '▾' : '▸'}</span>
              </button>
              <div className="step-metrics">
                <span title="尝试次数 / 工具预算">尝试 {s.attempts}/{s.callBudget}</span>
                <span title="证据卡数量">EvidenceCard {s.evidenceCardCount}</span>
                {s.remainingGaps.map((g) => (
                  <span key={g} className="gap-chip" title="剩余缺口">⚠ {g}</span>
                ))}
              </div>
              {isOpen && s.detail && <p className="step-detail">{s.detail}</p>}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
