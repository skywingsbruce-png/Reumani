import { useState } from 'react'
import { NavigationRail } from './NavigationRail'
import { ProjectSidebar } from './ProjectSidebar'
import { TaskHeader } from './TaskHeader'
import { PlanStepList } from './PlanStepList'
import { Timeline } from './Timeline'
import { ClarificationCard } from './ClarificationCard'
import { Composer } from './Composer'
import { TodoPanel } from './TodoPanel'
import { ArtifactPanel } from './ArtifactPanel'

export function AppShell() {
  const [rightOpen, setRightOpen] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className={`shell ${rightOpen ? '' : 'right-collapsed'}`}>
      <NavigationRail />

      <button className="sidebar-toggle" aria-label="切换项目栏" onClick={() => setSidebarOpen((v) => !v)}>☰</button>
      <div className={`sidebar-wrap ${sidebarOpen ? 'open' : ''}`}>
        <ProjectSidebar />
      </div>

      <main className="center" aria-label="任务工作区">
        <TaskHeader rightOpen={rightOpen} onToggleRight={() => setRightOpen((v) => !v)} />
        <div className="center-scroll">
          <PlanStepList />
          <ClarificationCard />
          <Timeline />
        </div>
        <Composer />
      </main>

      <aside className={`right ${rightOpen ? 'open' : ''}`} aria-label="待办与结果">
        <TodoPanel />
        <ArtifactPanel />
      </aside>
    </div>
  )
}
