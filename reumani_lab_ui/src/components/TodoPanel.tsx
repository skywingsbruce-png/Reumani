import { useLab } from '../store/LabStore'
import type { TodoItem } from '../types'

const KIND: Record<TodoItem['kind'], { label: string; g: string; c: string }> = {
  awaiting_answer: { label: '等待回答', g: '?', c: 'b-warn' },
  awaiting_approval: { label: '等待审批', g: '☐', c: 'b-warn' },
  needs_review: { label: '人工复核', g: '⚖', c: 'b-warn' },
  failed: { label: '失败', g: '✕', c: 'b-fail' },
}

export function TodoPanel() {
  const { state } = useLab()
  const todos = state.todos.filter((t) => t.taskId === state.currentTaskId)

  return (
    <div className="right-panel todos" aria-label="待办事项">
      <div className="section-head">
        <h2 className="section-title">待办 <span className="count" data-testid="todo-count">{todos.length}</span></h2>
      </div>
      {todos.length === 0 ? (
        <div className="empty" data-testid="todo-empty">
          <span aria-hidden>✓</span>
          <p>暂无待办。所有澄清与复核均已处理。</p>
        </div>
      ) : (
        <ul className="todo-list">
          {todos.map((t) => {
            const k = KIND[t.kind]
            return (
              <li key={t.id} className="todo">
                <span className={`badge ${k.c}`}><span className="g" aria-hidden>{k.g}</span>{k.label}</span>
                <span className={`todo-prio prio-${t.priority}`} title={`优先级：${t.priority}`}>{t.priority}</span>
                <div className="todo-title">{t.title}</div>
                <div className="todo-reason">{t.reason}</div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
