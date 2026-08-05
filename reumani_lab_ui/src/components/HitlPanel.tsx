import { useState } from 'react'
import { useLab } from '../store/LabStore'

interface Option { id: string; label: string }

function ClarificationCard({ pending }: { pending: Record<string, unknown> }) {
  const { state, answerClarification } = useLab()
  const requestId = String(pending.request_id)
  const kind = String(pending.kind ?? 'single_select')
  const multi = kind === 'multi_select'
  const allowOther = !!pending.allow_other
  const options = (pending.allowed_options as Option[]) ?? []
  const [sel, setSel] = useState<string[]>([])
  const [other, setOther] = useState('')
  const [useOther, setUseOther] = useState(false)

  const busy = state.controlBusy
  const canSubmit = !busy && (useOther ? other.trim().length > 0 : sel.length > 0)

  function toggle(id: string) {
    if (multi) setSel((s) => s.includes(id) ? s.filter((x) => x !== id) : [...s, id])
    else { setSel([id]); setUseOther(false) }
  }

  return (
    <section className="section hitl-card clar-sec" aria-label="澄清请求" data-testid="hitl-clarification">
      <div className="clar-head">
        <span className="clar-ic warn" aria-hidden>?</span>
        <strong>需要你的澄清</strong>
        <span className="badge b-warn"><span className="g" aria-hidden>⏸</span>等待澄清</span>
      </div>
      {pending.prompt ? (
        <p className="clar-prompt" data-testid="clar-prompt">{String(pending.prompt)}</p>
      ) : null}
      <p className="clar-q">{String(pending.reason ?? '请补充信息')}</p>
      <div className="clar-opts" role={multi ? 'group' : 'radiogroup'}>
        {options.map((o) => (
          <label key={o.id} className={`opt ${sel.includes(o.id) && !useOther ? 'sel' : ''}`}>
            <input type={multi ? 'checkbox' : 'radio'} name={`clar-${requestId}`}
              checked={sel.includes(o.id) && !useOther} onChange={() => toggle(o.id)} disabled={busy} />
            <span className="opt-body"><span className="opt-label">{o.label}</span>
              {pending.recommended === o.id
                ? <span className="badge b-ok rec-badge">推荐</span> : null}
            </span>
          </label>
        ))}
        {allowOther && (
          <label className={`opt ${useOther ? 'sel' : ''}`}>
            <input type="radio" name={`clar-${requestId}`} checked={useOther}
              onChange={() => { setUseOther(true); setSel([]) }} disabled={busy} />
            <span className="opt-body">
              <span className="opt-label">其他</span>
              {useOther && (
                <input className="clar-other" type="text" placeholder="输入其他组织来源…" maxLength={200}
                  value={other} onChange={(e) => setOther(e.target.value)} aria-label="其他（自定义）" />
              )}
            </span>
          </label>
        )}
      </div>
      <div className="clar-actions">
        <button className="btn primary" data-testid="clar-submit" disabled={!canSubmit}
          onClick={() => answerClarification(requestId, useOther ? [] : sel, useOther ? other.trim() : undefined)}>
          {busy ? '提交中…' : '提交澄清'}
        </button>
      </div>
    </section>
  )
}

function ApprovalCard({ pending }: { pending: Record<string, unknown> }) {
  const { state, decideApproval } = useLab()
  const requestId = String(pending.request_id)
  const actionHash = String(pending.action_hash)
  const risk = String(pending.risk_level ?? 'medium')
  const busy = state.controlBusy
  const research = pending.run_type === 'research'
  const policy = (pending.policy ?? {}) as Record<string, unknown>
  const stages = (Array.isArray(pending.stages) ? pending.stages : []) as string[]
  // A.7.5.5 §8：executor 自报的冻结事实。存在即代表这是一个接了受控真实模型的执行器，
  // 上限与证据边界必须如实展示给批准人（缺省回退到 spec/policy，不臆造数值）。
  const facts = (pending.frozen_facts ?? null) as Record<string, unknown> | null
  const fixture = pending.fixture === true
  const short = (v: unknown) => String(v ?? '').slice(0, 16)
  const cap = facts ? Number(facts.task_budget_usd ?? 0) : null
  const roles = (Array.isArray(facts?.roles) ? facts!.roles : []) as Array<Record<string, unknown>>
  return (
    <section className="section hitl-card approval-card" aria-label="审批请求" data-testid="hitl-approval">
      <div className="clar-head">
        <span className="clar-ic warn" aria-hidden>⚖</span>
        <strong>需要你的审批</strong>
        <span className={`badge ${risk === 'high' ? 'b-fail' : 'b-warn'}`}>风险：{risk}</span>
        {pending.is_simulation ? <span className="badge b-idle">仿真 / fake</span> : null}
      </div>
      <p className="clar-q">{String(pending.action_summary ?? '批准该动作')}</p>
      <dl className="approval-meta">
        {research ? (
          <>
            <div><dt>研究问题</dt><dd data-testid="apr-question">{String(pending.question ?? '')}</dd></div>
            <div><dt>澄清答复</dt><dd className="mono" data-testid="apr-answer">{String(pending.clarification_answer ?? '')}</dd></div>
            <div><dt>证据数量</dt><dd className="mono" data-testid="apr-evidence">
              {facts ? `${String(facts.core_evidence_count ?? 0)} 张核心证据卡（另有 ${String(facts.context_only_count ?? 0)} 张仅背景，不得作为实验依据）`
                     : `${String(pending.evidence_count ?? 0)} 条${fixture ? '（测试夹具）' : ''}`}
            </dd></div>
            {facts ? (
              <>
                <div><dt>证据构成</dt><dd className="mono" data-testid="apr-evidence-mix">
                  SSc 直接 {String(facts.direct_count ?? 0)} · 非 SSc 间接 {String(facts.indirect_count ?? 0)} ·
                  直接人体因果 {String(facts.direct_human_causal_count ?? 0)}
                </dd></div>
                <div><dt>因果上限</dt><dd className="mono" data-testid="apr-ceiling">{String(facts.causal_ceiling ?? '')}</dd></div>
                <div><dt>证据子集</dt><dd className="mono" data-testid="apr-subset">
                  {String(facts.subset_id ?? '')} · subset {short(facts.subset_hash)}…
                </dd></div>
                <div><dt>来源指纹</dt><dd className="mono" data-testid="apr-source-hash">
                  pack {short(facts.source_pack_hash)}… · protocol {short(facts.protocol_hash)}…
                </dd></div>
              </>
            ) : null}
            <div><dt>执行器</dt><dd className="mono" data-testid="apr-executor">{String(pending.executor_id ?? '')}</dd></div>
            <div><dt>执行阶段</dt><dd className="mono" data-testid="apr-stages">{stages.length} 阶段</dd></div>
            <div><dt>权限边界</dt><dd className="mono" data-testid="apr-policy">
              网络 {(facts ? facts.network_allowed : policy.allow_network) ? '允许' : '禁止'} ·
              代码 {(facts ? facts.code_allowed : policy.allow_code_execution) ? '允许' : '禁止'} ·
              设备 {(facts ? facts.device_allowed : policy.allow_device_control) ? '允许' : '禁止'} ·
              Planner {(facts ? facts.planner_allowed : policy.allow_planner) ? '允许' : '禁止'}
            </dd></div>
            <div><dt>调用上限</dt><dd className="mono" data-testid="apr-calls">
              总计 {String(facts?.total_call_cap ?? policy.max_model_calls ?? 0)} ·
              {' '}{roles.length || 3} 个角色各 1（不可互借）
            </dd></div>
            {roles.length ? (
              <div><dt>角色与输出上限</dt><dd className="mono" data-testid="apr-roles">
                {roles.map(r => `${String(r.role)}:${String(r.model_id)} `
                  + `max_tokens=${String(r.max_tokens)} `
                  + `worst=US$${Number(r.worst_case_cost_usd ?? 0).toFixed(5)}`).join(' · ')}
              </dd></div>
            ) : null}
            <div><dt>费用上限</dt><dd className="mono" data-testid="apr-cost-cap">
              {cap !== null ? `US$${cap.toFixed(5)}（硬闸门）` : '不适用（零付费夹具）'}
            </dd></div>
            {facts ? (
              <div><dt>最坏费用</dt><dd className="mono" data-testid="apr-worst-cost">
                US${Number(facts.worst_case_cost_usd ?? 0).toFixed(5)}
                {cap ? `（占预算 ${(100 * Number(facts.worst_case_cost_usd ?? 0) / cap).toFixed(1)}%）` : ''}
              </dd></div>
            ) : null}
            {facts ? (
              <div><dt>证据层级</dt><dd className="mono" data-testid="apr-content-level">
                {String(facts.evidence_content_level ?? 'abstract_only')}（仅摘要级，不含全文）
              </dd></div>
            ) : null}
            <div><dt>预期产物</dt><dd className="mono">
              {facts ? String(facts.expected_artifact ?? '') : (pending.expected_outputs as string[] ?? []).join(', ')}
            </dd></div>
            <div><dt>策略指纹</dt><dd className="mono">{short(pending.policy_hash)}…</dd></div>
            {facts ? (
              <div><dt>冻结事实指纹</dt><dd className="mono" data-testid="apr-facts-hash">
                {short(facts.preview_hash)}…
              </dd></div>
            ) : null}
          </>
        ) : (
          <div><dt>工具</dt><dd className="mono">{String(pending.tool_name)}</dd></div>
        )}
        <div><dt>预期副作用</dt><dd>{String(pending.expected_side_effect)}</dd></div>
        <div><dt>动作指纹</dt><dd className="mono">{actionHash.slice(0, 16)}…</dd></div>
      </dl>
      <p className="approval-note">
        仅在动作/参数完全一致时有效；更改后需重新申请。
        {research
          // 判据是**是否挂了非零预算的硬闸门**，而不是证据是否为夹具：
          // 前端无法分辨 provider 真假，宁可多警告，绝不可少警告。
          ? (cap !== null && cap > 0
              ? `批准后将冻结问题、证据、策略与执行器，并按上述硬上限调用受控付费模型（最多 ${String(facts?.total_call_cap ?? 3)} 次，费用上限 US$${cap.toFixed(5)}）。批准即授权计费。`
              : '批准后将冻结问题、证据、策略与执行器；此为零付费测试夹具，不调用真实模型。')
          : '此为仿真，不连接真实设备。'}
      </p>
      <div className="clar-actions">
        <button className="btn primary" data-testid="approve-btn" disabled={busy}
          onClick={() => decideApproval(requestId, true, actionHash)}>{busy ? '处理中…' : '批准'}</button>
        <button className="btn danger" data-testid="deny-btn" disabled={busy}
          onClick={() => decideApproval(requestId, false, actionHash)}>拒绝</button>
      </div>
    </section>
  )
}

export function HitlPanel() {
  const { state } = useLab()
  if (state.mode !== 'api') return null
  const p = state.pending
  return (
    <>
      {state.controlError && (
        <div className="hitl-error" role="alert" data-testid="control-error">⚠ {state.controlError}</div>
      )}
      {p?.type === 'clarification' && <ClarificationCard pending={p} />}
      {p?.type === 'approval' && <ApprovalCard pending={p} />}
    </>
  )
}
