import { useLab } from '../store/LabStore'
import { tlGlyph } from '../ui'
import { TracePanel } from './TracePanel'

const TYPE_LABEL: Record<string, string> = {
  user_message: '用户消息', plan_created: '制定计划', tool_selected: '选择工具',
  tool_started: '工具执行', observation: 'Observation', evidence_card: 'EvidenceCard',
  clarification_requested: '请求澄清', clarification_answered: '澄清已回答', resumed: '恢复执行',
  verifier_result: 'Verifier 结果', artifact_created: '产出物', stopped: '已停止',
}

export function Timeline() {
  const { state } = useLab()
  const events = state.timeline.filter((e) => e.taskId === state.currentTaskId)

  return (
    <section className="section timeline-sec" aria-label="任务时间线">
      <div className="section-head">
        <h2 className="section-title">任务时间线</h2>
        <TracePanel />
      </div>
      <ol className="timeline" data-testid="timeline">
        {events.map((e) => {
          const g = tlGlyph[e.type] ?? tlGlyph.observation
          return (
            <li key={e.id} className="tl-item">
              <span className="tl-ic" aria-hidden style={{ background: g.bg, color: g.fg }}>{g.g}</span>
              <div className="tl-body">
                <div className="tl-head">
                  <span className="tl-type">{TYPE_LABEL[e.type] ?? e.type}</span>
                  <span className="tl-title">{e.title}</span>
                  <span className="tl-at mono">{e.at}</span>
                </div>
                {e.detail && <div className="tl-detail">{e.detail}</div>}
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
