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
            <div><dt>证据数量</dt><dd className="mono" data-testid="apr-evidence">{String(pending.evidence_count ?? 0)} 条（测试夹具）</dd></div>
            <div><dt>执行器</dt><dd className="mono" data-testid="apr-executor">{String(pending.executor_id ?? '')}</dd></div>
            <div><dt>执行阶段</dt><dd className="mono" data-testid="apr-stages">{stages.length} 阶段</dd></div>
            <div><dt>权限边界</dt><dd className="mono" data-testid="apr-policy">
              网络 {policy.allow_network ? '允许' : '禁止'} · 代码 {policy.allow_code_execution ? '允许' : '禁止'} ·
              设备 {policy.allow_device_control ? '允许' : '禁止'} · Planner {policy.allow_planner ? '允许' : '禁止'}
            </dd></div>
            <div><dt>调用上限</dt><dd className="mono">
              总计 {String(policy.max_model_calls ?? 0)} · 角色各 1（不可互借）
            </dd></div>
            <div><dt>预期产物</dt><dd className="mono">{(pending.expected_outputs as string[] ?? []).join(', ')}</dd></div>
            <div><dt>策略指纹</dt><dd className="mono">{String(pending.policy_hash ?? '').slice(0, 16)}…</dd></div>
          </>
        ) : (
          <div><dt>工具</dt><dd className="mono">{String(pending.tool_name)}</dd></div>
        )}
        <div><dt>预期副作用</dt><dd>{String(pending.expected_side_effect)}</dd></div>
        <div><dt>动作指纹</dt><dd className="mono">{actionHash.slice(0, 16)}…</dd></div>
      </dl>
      <p className="approval-note">
        仅在动作/参数完全一致时有效；更改后需重新申请。
        {research ? '批准后将冻结问题、证据、策略与执行器；此为零付费测试夹具，不调用真实模型。'
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
