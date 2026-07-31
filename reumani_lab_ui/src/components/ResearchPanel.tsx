import { useLab } from '../store/LabStore'

const STAGE_LABEL: Record<string, string> = {
  validate_evidence: '校验证据',
  evidence_accumulator: '汇总证据',
  synthesizer: '综合（Synthesizer）',
  verifier: '核查（Verifier）',
  claim_extractor: '提取 Claim',
  claim_graph: '构建 Claim 图',
  shadow: 'Shadow 比对',
  artifact_builder: '生成产物',
}
const ALL_STAGES = Object.keys(STAGE_LABEL)

/** Research run 阶段时间线 + Verifier/Shadow 裁决（A.7.5.3）。demo run 不渲染。 */
export function ResearchPanel() {
  const { state } = useLab()
  if (state.mode !== 'api' || state.runType !== 'research') return null
  const r = (state.research ?? {}) as Record<string, unknown>
  const done = (Array.isArray(r.stages_done) ? r.stages_done : []) as string[]
  const stages = (Array.isArray(r.stages) && (r.stages as string[]).length
    ? r.stages : ALL_STAGES) as string[]
  const current = r.current_stage as string | null
  const verifier = r.verifier_verdict as string | undefined
  const shadow = r.shadow_verdict as string | undefined
  const tier = r.causal_tier as string | undefined

  return (
    <section className="section research-sec" aria-label="研究阶段" data-testid="research-panel">
      <div className="research-head">
        <h2 className="section-title">
          研究链阶段 <span className="count">{done.length}/{stages.length}</span>
        </h2>
        <span className="badge b-idle" data-testid="research-fixture">测试夹具 / fixture</span>
        {r.executor_id ? (
          <span className="badge b-idle mono" data-testid="research-executor">{String(r.executor_id)}</span>
        ) : null}
      </div>

      <ol className="stage-list">
        {stages.map((s) => {
          const isDone = done.includes(s)
          const isCur = current === s && !isDone
          return (
            <li key={s} className={`stage ${isDone ? 'done' : isCur ? 'cur' : 'todo'}`}
                data-testid={`stage-${s}`} data-state={isDone ? 'done' : isCur ? 'running' : 'todo'}>
              <span className="stage-ic" aria-hidden>{isDone ? '✓' : isCur ? '▶' : '○'}</span>
              <span className="stage-name">{STAGE_LABEL[s] ?? s}</span>
            </li>
          )
        })}
      </ol>

      {(verifier || shadow) && (
        <dl className="verdicts">
          {verifier && (
            <div><dt>Verifier（最终裁决）</dt>
              <dd className="mono" data-testid="verifier-verdict">{verifier}</dd></div>
          )}
          {tier && (
            <div><dt>因果等级</dt><dd className="mono" data-testid="causal-tier">{tier}</dd></div>
          )}
          {shadow && (
            <div><dt>Shadow（仅记录，不翻转）</dt>
              <dd className="mono" data-testid="shadow-verdict">{shadow}</dd></div>
          )}
        </dl>
      )}
    </section>
  )
}
