import type { Project, TaskSession } from '../types'

export const mockProjects: Project[] = [
  { id: 'proj-ssc-pigr', name: 'SSc / pIgR Project', subtitle: 'Systemic sclerosis · secretory immunity' },
  { id: 'proj-ssc-fibro', name: 'SSc Fibrosis Atlas', subtitle: 'Single-cell & mechanism' },
]

export const mockTasks: TaskSession[] = [
  {
    id: 'task-pigr',
    projectId: 'proj-ssc-pigr',
    title: 'pIgR-mediated antibody internalization',
    status: 'awaiting_input',
    updatedAt: '2026-07-24T09:12:00',
    group: 'awaiting_input',
  },
  {
    id: 'task-il6',
    projectId: 'proj-ssc-pigr',
    title: 'IL-6 causal evidence review',
    status: 'running',
    updatedAt: '2026-07-24T09:40:00',
    group: 'running',
  },
  {
    id: 'task-crispr',
    projectId: 'proj-ssc-pigr',
    title: 'CRISPR guide RNA design',
    status: 'completed',
    updatedAt: '2026-07-23T18:05:00',
    group: 'completed',
  },
  {
    id: 'task-scrna',
    projectId: 'proj-ssc-pigr',
    title: 'SSc single-cell dataset review',
    status: 'failed',
    updatedAt: '2026-07-23T16:20:00',
    group: 'failed',
  },
]
