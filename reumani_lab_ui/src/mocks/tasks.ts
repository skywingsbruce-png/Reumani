import type {
  ClarificationRequest, PlanStep, TimelineEvent, TodoItem, TraceEvent,
} from '../types'

// Plan steps for the active demo task (IL-6 causal evidence review).
export const mockPlanSteps: PlanStep[] = [
  {
    id: 'step-1', taskId: 'task-il6', index: 1, objective: '检索文献 (search_literature)',
    status: 'satisfied', attempts: 2, callBudget: 2, evidenceCardCount: 3, remainingGaps: [],
    detail: '两次结构化检索；第二次为 transport-only（无新证据），步骤终止。',
  },
  {
    id: 'step-2', taskId: 'task-il6', index: 2, objective: '标准化证据 (normalize + EvidenceCard)',
    status: 'running', attempts: 1, callBudget: 1, evidenceCardCount: 3, remainingGaps: ['1 篇待补研究设计'],
    detail: '把结构化文献记录归一为 EvidenceCard，标注 content_level 与研究设计。',
  },
  {
    id: 'step-3', taskId: 'task-il6', index: 3, objective: '判断因果强度 (causal calibration)',
    status: 'blocked', attempts: 0, callBudget: 1, evidenceCardCount: 0,
    remainingGaps: ['等待浓度/时间澄清'],
    detail: '区分 association / temporal / intervention；缺时序与干预证据不得升为因果。',
  },
  {
    id: 'step-4', taskId: 'task-il6', index: 4, objective: '独立核查 (Verifier)',
    status: 'pending', attempts: 0, callBudget: 2, evidenceCardCount: 0, remainingGaps: [],
  },
  {
    id: 'step-5', taskId: 'task-il6', index: 5, objective: '生成报告 (Evidence report)',
    status: 'pending', attempts: 0, callBudget: 1, evidenceCardCount: 0, remainingGaps: [],
  },
]

export const mockTimeline: TimelineEvent[] = [
  { id: 'ev-1', taskId: 'task-il6', type: 'user_message', at: '09:31', title: '用户提出问题', detail: 'IL-6 与皮肤评分的因果证据现状？' },
  { id: 'ev-2', taskId: 'task-il6', type: 'plan_created', at: '09:31', title: 'Agent 制定计划', detail: '5 步有界收敛计划' },
  { id: 'ev-3', taskId: 'task-il6', type: 'tool_selected', at: '09:32', title: '工具被选择', detail: 'search_literature' },
  { id: 'ev-4', taskId: 'task-il6', type: 'tool_started', at: '09:32', title: '工具开始执行', status: 'running' },
  { id: 'ev-5', taskId: 'task-il6', type: 'observation', at: '09:32', title: 'Observation 返回', detail: 'success · 3 records · abstract' },
  { id: 'ev-6', taskId: 'task-il6', type: 'evidence_card', at: '09:33', title: 'EvidenceCard 构建', detail: '3 张 · 横断面 · metadata_only→abstract' },
  { id: 'ev-7', taskId: 'task-il6', type: 'clarification_requested', at: '09:34', title: '等待用户澄清', status: 'blocked', detail: '抗体浓度与处理时间冲突' },
]

export const mockTrace: TraceEvent[] = [
  { eventIndex: 1, stage: 'collect', toolName: 'search_literature', status: 'ok', durationMs: 5499, structured: 'structured', evidenceCount: 3, resultHashShort: '0d37…4ac3' },
  { eventIndex: 2, stage: 'collect', toolName: 'search_literature', status: 'ok', durationMs: 5258, structured: 'structured', evidenceCount: 0, resultHashShort: '1f97…3f70' },
  { eventIndex: 3, stage: 'normalize', toolName: 'evidence_build', status: 'ok', durationMs: 210, structured: 'structured', evidenceCount: 3, resultHashShort: 'ca93…71be' },
  { eventIndex: 4, stage: 'assess_step', toolName: 'causal_calibrator', status: 'blocked', durationMs: 0, structured: 'structured', evidenceCount: 0, resultHashShort: '—' },
]

export const mockClarifications: ClarificationRequest[] = [
  {
    id: 'clar-conc', taskId: 'task-il6', stepId: 'step-3', answered: false,
    question: '当前记录中抗体浓度出现 30 μg/mL 和 40 μg/mL 两个版本。继续生成实验方案前，请确认本次采用哪一个？',
    options: [
      { id: 'c-30', label: '30 μg/mL', recommended: true, reason: '与最新上传协议一致，且样本量更大' },
      { id: 'c-40', label: '40 μg/mL' },
      { id: 'c-40-total', label: '40 μg 总量' },
      { id: 'c-other', label: '输入其他浓度', allowFreeText: true },
    ],
  },
  {
    id: 'clar-time', taskId: 'task-il6', stepId: 'step-3', answered: false,
    question: '处理时间在不同记录中不一致。请确认本次采用的处理条件：',
    options: [
      { id: 't-20h', label: '10 μM × 20 h', recommended: true, reason: '为多数记录采用的标准条件' },
      { id: 't-unknown', label: '10 μM，但时间未知' },
      { id: 't-existing', label: '使用已有记录' },
      { id: 't-custom', label: '自定义', allowFreeText: true },
    ],
  },
]

export const mockTodos: TodoItem[] = [
  { id: 'todo-conc', taskId: 'task-il6', kind: 'awaiting_answer', title: '确认抗体浓度', reason: '30 vs 40 μg/mL 冲突', stepId: 'step-3', priority: 'high', clarificationId: 'clar-conc' },
  { id: 'todo-time', taskId: 'task-il6', kind: 'awaiting_answer', title: '确认处理时间', reason: '处理时间记录不一致', stepId: 'step-3', priority: 'medium', clarificationId: 'clar-time' },
  { id: 'todo-review', taskId: 'task-il6', kind: 'needs_review', title: '人工复核因果表述', reason: '横断面证据不得升为因果', stepId: 'step-4', priority: 'medium' },
]
