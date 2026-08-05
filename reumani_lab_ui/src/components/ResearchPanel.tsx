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
  const failedStage = r.failed_stage as string | undefined
  const errorType = r.error_type as string | undefined
  const errorSummary = r.error_summary as string | undefined
  const interrupted = r.interrupted_stage as string | undefined
  const truncated = r.output_truncated === true
  const needsReview = r.human_review === true

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
          const isFail = failedStage === s
          const isCur = current === s && !isDone && !isFail
          const st = isFail ? 'failed' : isDone ? 'done' : isCur ? 'running' : 'todo'
          return (
            <li key={s} className={`stage ${st}`} data-testid={`stage-${s}`} data-state={st}>
              <span className="stage-ic" aria-hidden>
                {isFail ? '✕' : isDone ? '✓' : isCur ? '▶' : '○'}
              </span>
              <span className="stage-name">{STAGE_LABEL[s] ?? s}</span>
            </li>
          )
        })}
      </ol>

      {failedStage && (
        <div className="research-failed" role="alert" data-testid="research-failure">
          <strong>执行失败：{STAGE_LABEL[failedStage] ?? failedStage}</strong>
          <span className="mono" data-testid="failed-stage">{failedStage}</span>
          {errorType && <span className="mono" data-testid="failure-type">{errorType}</span>}
          {truncated && (
            // A.7.5.6.1 §11：截断必须一眼可辨（≠ 普通 schema 错误），
            // 但只显示元数据，绝不显示被截断的原始输出。
            <span className="mono badge b-fail" data-testid="failure-truncated">
              output_truncated · {String(r.truncated_role ?? failedStage)}
              {r.finish_reason ? ` · finish_reason=${String(r.finish_reason)}` : ''}
              {r.output_size != null
                ? ` · ${String(r.output_size)}/${String(r.configured_output_limit ?? '?')} tokens`
                : ''}
            </span>
          )}
          {errorSummary && <p className="failure-summary">{errorSummary}</p>}
          {needsReview && (
            <span className="mono" data-testid="failure-human-review">需人工复核</span>
          )}
          <p className="approval-note">未生成科研产物；需人工审查（human review）。</p>
        </div>
      )}
      {!failedStage && interrupted && (
        <div className="research-failed" role="alert" data-testid="research-interrupted">
          <strong>阶段执行结果不确定：{STAGE_LABEL[interrupted] ?? interrupted}</strong>
          <p className="approval-note">重启后未自动重放；需人工审查后决定。</p>
        </div>
      )}

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
