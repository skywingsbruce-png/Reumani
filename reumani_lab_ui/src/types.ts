// Reumani Lab — UI-0 type contracts.
// These mirror (loosely) the backend open-task contracts so the mock service can later
// be swapped for a real API/SSE layer without changing components.

export type TaskStatus =
  | 'awaiting_input'
  | 'running'
  | 'completed'
  | 'failed'

export type StepStatus =
  | 'pending'
  | 'running'
  | 'satisfied'
  | 'insufficient'
  | 'failed'
  | 'blocked'

export type ParseStatus = 'parsed' | 'parsing' | 'unparsed' | 'error'

export type ProvenanceStatus = 'verified' | 'pending' | 'missing'

export type VerifierStatus = 'passed' | 'not_passed' | 'insufficient_for_causal' | 'pending' | 'not_run'

export type RuntimePhase = 'idle' | 'running' | 'stopping' | 'stopped'

export interface Project {
  id: string
  name: string
  subtitle?: string
}

export interface TaskSession {
  id: string
  projectId: string
  title: string
  status: TaskStatus
  updatedAt: string // ISO
  group: 'awaiting_input' | 'running' | 'completed' | 'failed'
}

export interface FileAsset {
  id: string
  name: string
  kind: 'pdf' | 'csv' | 'json' | 'image' | 'fasta' | 'other'
  sizeBytes: number
  uploadedAt: string
  parseStatus: ParseStatus
  provenanceStatus: ProvenanceStatus
  hashShort?: string
  referencedByTaskId?: string | null
  mock: true
}

export interface PlanStep {
  id: string
  taskId: string
  index: number
  objective: string
  status: StepStatus
  attempts: number
  callBudget: number
  evidenceCardCount: number
  remainingGaps: string[]
  detail?: string
}

export type TimelineEventType =
  | 'user_message'
  | 'plan_created'
  | 'tool_selected'
  | 'tool_started'
  | 'observation'
  | 'evidence_card'
  | 'clarification_requested'
  | 'clarification_answered'
  | 'resumed'
  | 'verifier_result'
  | 'artifact_created'
  | 'stopped'

export interface TimelineEvent {
  id: string
  taskId: string
  type: TimelineEventType
  at: string
  title: string
  detail?: string
  status?: string
}

export interface TraceEvent {
  eventIndex: number
  stage: string
  toolName: string
  status: string
  durationMs: number
  structured: 'structured' | 'legacy'
  evidenceCount: number
  resultHashShort: string
}

export interface ClarificationOption {
  id: string
  label: string
  recommended?: boolean
  reason?: string
  allowFreeText?: boolean
}

export interface ClarificationRequest {
  id: string
  taskId: string
  stepId: string
  question: string
  options: ClarificationOption[]
  answered: boolean
  answerLabel?: string
}

export interface TodoItem {
  id: string
  taskId: string
  kind: 'awaiting_answer' | 'awaiting_approval' | 'needs_review' | 'failed'
  title: string
  reason: string
  stepId?: string
  priority: 'high' | 'medium' | 'low'
  clarificationId?: string
}

export type ArtifactPreviewKind = 'markdown' | 'csv' | 'image' | 'json'

export interface Artifact {
  id: string
  taskId: string
  name: string
  kind: 'md' | 'json' | 'pdf' | 'png' | 'csv' | 'jsonl'
  sizeBytes: number
  createdAt: string
  stepId?: string
  verifierStatus: VerifierStatus
  provenanceStatus: ProvenanceStatus
  hashShort?: string
  previewKind: ArtifactPreviewKind
  preview: string // desensitized mock preview content
}

export interface RuntimeState {
  phase: RuntimePhase
  elapsedMs: number
}
