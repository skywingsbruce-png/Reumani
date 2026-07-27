import { useLab } from '../store/LabStore'

// Clearly marks a canary run so it is never mistaken for a normal DEMO.
export function CanaryBanner() {
  const { state } = useLab()
  const m = state.canaryMeta
  if (!m) return null
  const real = m.canary_kind === 'real'
  const calls = Number(m.model_calls ?? 0)
  const cost = Number(m.usd_cost ?? 0)
  const tier = String(m.causal_tier ?? '—')
  return (
    <div className={`canary-banner ${real ? 'real' : 'fake'}`} data-testid="canary-banner" role="note">
      <span className="canary-tag" aria-hidden>🔬</span>
      <strong>{real ? 'Real model canary' : 'Model canary（fake provider）'}</strong>
      <span className="canary-sep">·</span>
      <span>Frozen real literature evidence</span>
      <span className="canary-metrics">
        真实模型调用 <b data-testid="canary-calls">{calls}</b> · 费用 <b data-testid="canary-cost">${cost.toFixed(4)}</b>
        {' '}· 因果等级 <b>{tier}</b>
      </span>
      {!real && <span className="canary-note">（fake provider：$0，用于链路演练）</span>}
    </div>
  )
}
