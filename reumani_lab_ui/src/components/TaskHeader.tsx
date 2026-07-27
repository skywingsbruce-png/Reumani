import { useLab } from '../store/LabStore'
import { TaskBadge } from '../ui'
import { RuntimeControl } from './RuntimeControl'
import { CanaryBanner } from './CanaryBanner'

interface Props {
  rightOpen: boolean
  onToggleRight: () => void
}

export function TaskHeader({ rightOpen, onToggleRight }: Props) {
  const { currentProject, currentTask } = useLab()
  const proj = currentProject()
  const task = currentTask()

  return (
    <header className="task-header">
      <div className="task-header-main">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <span>{proj?.name ?? '项目'}</span>
          <span className="sep" aria-hidden>/</span>
          <span className="cur">{task?.title ?? '任务'}</span>
        </nav>
        <div className="task-title-row">
          <h1 className="task-title-h">{task?.title ?? '任务'}</h1>
          {task && <TaskBadge status={task.status} />}
        </div>
        <CanaryBanner />
      </div>
      <div className="task-header-actions">
        <RuntimeControl />
        <button className="btn subtle" title="分享（占位）" aria-label="分享（占位，未启用）" disabled>⤴ 分享</button>
        <button className="btn subtle" aria-pressed={rightOpen} onClick={onToggleRight}
          title="切换右侧面板">⧉ 面板</button>
      </div>
    </header>
  )
}
