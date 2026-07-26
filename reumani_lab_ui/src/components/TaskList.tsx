import { useLab } from '../store/LabStore'
import { taskDot } from '../ui'
import type { TaskSession } from '../types'

const GROUPS: { key: TaskSession['group']; label: string }[] = [
  { key: 'awaiting_input', label: '等待输入' },
  { key: 'running', label: '运行中' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败 / 需复核' },
]

export function TaskList() {
  const { state, dispatch } = useLab()
  const q = state.taskSearch.trim().toLowerCase()
  const tasks = state.tasks
    .filter((t) => t.projectId === state.currentProjectId)
    .filter((t) => !q || t.title.toLowerCase().includes(q))

  return (
    <div className="task-groups">
      {GROUPS.map((grp) => {
        const items = tasks.filter((t) => t.group === grp.key)
        if (!items.length) return null
        return (
          <div key={grp.key} className="task-group">
            <div className="task-group-label">{grp.label}<span className="count">{items.length}</span></div>
            <ul className="task-ul" role="listbox" aria-label={grp.label}>
              {items.map((t) => (
                <li key={t.id}>
                  <button
                    className={`task-item ${t.id === state.currentTaskId ? 'active' : ''}`}
                    role="option" aria-selected={t.id === state.currentTaskId}
                    onClick={() => dispatch({ type: 'select_task', id: t.id })}
                  >
                    <span className="task-dot" aria-hidden style={{ background: taskDot(t.status) }} />
                    <span className="task-title">{t.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </div>
  )
}
