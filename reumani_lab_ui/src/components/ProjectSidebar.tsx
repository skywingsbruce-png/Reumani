import { useLab } from '../store/LabStore'
import { TaskList } from './TaskList'
import { FileAssetPanel } from './FileAssetPanel'

export function ProjectSidebar() {
  const { state, dispatch, currentProject } = useLab()
  const proj = currentProject()

  return (
    <aside className="sidebar" aria-label="项目与任务">
      <div className="proj-switch">
        <label className="proj-label" htmlFor="proj-select">当前项目</label>
        <select id="proj-select" className="proj-select" value={state.currentProjectId}
          onChange={(e) => dispatch({ type: 'select_project', id: e.target.value })}>
          {state.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {proj?.subtitle && <div className="proj-sub">{proj.subtitle}</div>}
      </div>

      <button className="btn primary new-task">＋ 新建任务</button>

      <input className="search" type="search" placeholder="搜索任务…" aria-label="搜索任务"
        value={state.taskSearch} onChange={(e) => dispatch({ type: 'set_task_search', q: e.target.value })} />

      <TaskList />
      <div className="sidebar-divider" />
      <FileAssetPanel />
    </aside>
  )
}
